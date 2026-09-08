import os
import re
import time
from enum import Enum
from aws_lambda_powertools import Logger
from langchain.callbacks.base import BaseCallbackHandler
from langchain.chains.conversation.base import ConversationChain
from langchain.chains import ConversationalRetrievalChain
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.memory import ConversationBufferMemory
from langchain.prompts.prompt import PromptTemplate
from langchain.chains.conversational_retrieval.prompts import (
    QA_PROMPT,
    CONDENSE_QUESTION_PROMPT,
)
from typing import Dict, List, Any

from genai_core.langchain import (
    SanitizingWorkspaceRetriever,
    DynamoDBChatMessageHistory,
)
import genai_core.citations
from genai_core.observability import ai_metrics
from genai_core.observability import pricing as pricing_config
from genai_core.types import ChatbotMode
from genai_core.types import CommonError
from genai_core.clients import get_bedrock_client

from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.outputs import LLMResult, ChatGeneration
from langchain_core.messages.ai import AIMessage, AIMessageChunk
from langchain_core.messages.human import HumanMessage
from langchain_aws import ChatBedrockConverse

logger = Logger()


class Mode(Enum):
    CHAIN = "chain"


class LLMStartHandler(BaseCallbackHandler):
    prompts = []
    usage = None

    # Langchain callbacks
    # https://python.langchain.com/v0.2/docs/concepts/#callbacks
    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        for prompt in prompts:
            self.prompts.append(prompt)

    def on_llm_end(
        self, response: LLMResult, *, run_id, parent_run_id, **kwargs: Any
    ) -> Any:
        generation = response.generations[0][0]  # only one llm request
        if (
            generation is not None
            and isinstance(generation, ChatGeneration)
            and isinstance(generation.message, AIMessage)
        ):
            # In case of rag there could be 2 llm calls.
            if self.usage is None:
                self.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
            self.usage = {
                "input_tokens": self.usage.get("input_tokens", 0)
                + generation.message.usage_metadata.get("input_tokens", 0),
                "output_tokens": self.usage.get("output_tokens", 0)
                + generation.message.usage_metadata.get("output_tokens", 0),
                "total_tokens": self.usage.get("total_tokens", 0)
                + generation.message.usage_metadata.get("total_tokens", 0),
            }


class ModelAdapter:
    def __init__(
        self,
        session_id,
        user_id,
        mode=ChatbotMode.CHAIN.value,
        disable_streaming=False,
        model_kwargs={},
    ):
        self.session_id = session_id
        self.user_id = user_id
        self._mode = mode
        self.model_kwargs = model_kwargs
        # Disable streaming since the guardrails are applied after the full response
        # With the exception of Bedrock models
        self.disable_streaming = (
            disable_streaming or self.should_call_apply_bedrock_guardrails()
        )

        self.callback_handler = LLMStartHandler()
        self.__bind_callbacks()

        self.chat_history = self.get_chat_history()
        self.llm = self.get_llm(model_kwargs)

    def __bind_callbacks(self):
        callback_methods = [method for method in dir(self) if method.startswith("on_")]
        valid_callback_names = [
            attr for attr in dir(self.callback_handler) if attr.startswith("on_")
        ]

        for method in callback_methods:
            if method in valid_callback_names:
                setattr(self.callback_handler, method, getattr(self, method))

    def get_bedrock_guardrails(self) -> dict:
        if "BEDROCK_GUARDRAILS_ID" in os.environ:
            return {
                "guardrailIdentifier": os.environ["BEDROCK_GUARDRAILS_ID"],
                "guardrailVersion": os.environ.get(
                    "BEDROCK_GUARDRAILS_VERSION", "DRAFT"
                ),
            }
        return {}

    def should_call_apply_bedrock_guardrails(self) -> bool:
        guardrails = self.get_bedrock_guardrails()
        return len(guardrails.keys()) > 0

    def apply_bedrock_guardrails(self, source: str, content: str):
        if self.should_call_apply_bedrock_guardrails():
            bedrock = get_bedrock_client()
            guardrails = self.get_bedrock_guardrails()
            response = bedrock.apply_guardrail(
                guardrailIdentifier=guardrails.get("guardrailIdentifier"),
                guardrailVersion=guardrails.get("guardrailVersion"),
                source=source,
                content=[
                    {
                        "text": {
                            "text": content,
                        }
                    },
                ],
            )
            if response.get("action") == "GUARDRAIL_INTERVENED":
                outputs = response.get("outputs")
                return {
                    "sessionId": self.session_id,
                    "type": "text",
                    # This message is the one configured in the
                    # bedrock guardrail settings
                    "content": (
                        response.get("outputs")[0].get("text")
                        if len(outputs) > 0
                        else "I cannot answer this question."
                    ),
                }
        else:
            return None

    def add_files_to_message_history(self, images=[], documents=[], videos=[]):
        # Needs to be implemented per adapter. (For example Bedrock needs to use base64)
        if len(images) > 0 or len(documents) > 0 or len(videos) > 0:
            raise CommonError("Adapter does not support files as a input")
        return

    def get_endpoint(self, model_id):
        clean_name = "SAGEMAKER_ENDPOINT_" + re.sub(r"[\s.\/\-_]", "", model_id).upper()
        if os.getenv(clean_name):
            return os.getenv(clean_name)
        else:
            return model_id

    def get_llm(self, model_kwargs={}):
        raise ValueError("llm must be implemented")

    def get_embeddings_model(self, embeddings):
        raise ValueError("embeddings must be implemented")

    def get_chat_history(self):
        return DynamoDBChatMessageHistory(
            table_name=os.environ["SESSIONS_TABLE_NAME"],
            session_id=self.session_id,
            user_id=self.user_id,
        )

    def get_memory(self, output_key=None, return_messages=False):
        return ConversationBufferMemory(
            memory_key="chat_history",
            chat_memory=self.chat_history,
            return_messages=return_messages,
            output_key=output_key,
        )

    def _emit_token_and_cost_metrics(self):
        """A4 — shared helper called from every chain path
        (run_with_chain_v2, and both branches of the legacy
        run_with_chain) so the same input/output token + estimated cost
        metrics are emitted consistently everywhere, without duplicating
        this logic three times.

        Reads ONLY self.callback_handler.usage (the real, provider-
        returned token counts already captured by LLMStartHandler above
        in this file — see its on_llm_end) and self.model_id. Never
        computes or guesses a token count. See
        genai_core.observability.ai_metrics / .pricing for what happens
        when usage data or pricing is unavailable (both skip emitting
        rather than emitting a fabricated value).
        """
        input_tokens = None
        output_tokens = None
        if self.callback_handler.usage is not None:
            input_tokens = self.callback_handler.usage.get("input_tokens")
            output_tokens = self.callback_handler.usage.get("output_tokens")
        ai_metrics.log_input_tokens(self.model_id, input_tokens)
        ai_metrics.log_output_tokens(self.model_id, output_tokens)
        estimated_cost = pricing_config.estimate_cost_usd(
            self.model_id, input_tokens, output_tokens
        )
        ai_metrics.log_estimated_cost(self.model_id, estimated_cost)

    def get_prompt(self, custom_prompt=None):
        template = """The following is a friendly conversation between a human and an AI. If the AI does not know the answer to a question, it truthfully says it does not know.

        Current conversation:
        {chat_history}

        Question: {input}"""  # noqa: E501

        return PromptTemplate.from_template(template)

    def get_condense_question_prompt(self, custom_prompt=None):
        return CONDENSE_QUESTION_PROMPT

    def get_qa_prompt(self, custom_prompt=None):
        return QA_PROMPT

    def generate_image(self, input: dict, files=None):
        raise CommonError("This adapter does not support image generation")

    def generate_video(self, input: dict, files=None):
        raise CommonError("This adapter does not support video generation")

    def format_prompt(self, prompt, messages, files):
        # Needs to be implemented per adapter. (For example Bedrock needs to use base64)
        raise CommonError("Prompt formatting not supported for this adapter")

    def run_with_chain_v2(
        self,
        user_prompt,
        workspace_id=None,
        images=[],
        sessions_documents=[],
        videos=[],
        user_groups=None,
        system_prompts={},
    ):
        if not self.llm:
            raise ValueError("llm must be set")

        self.callback_handler.prompts = []
        workspace_documents = []
        retriever = None

        if workspace_id:
            retriever = SanitizingWorkspaceRetriever(workspace_id=workspace_id)
            # Only stream the last llm call (otherwise the internal
            # llm response will be visible)
            llm_without_streaming = self.get_llm({"streaming": False})
            history_aware_retriever = create_history_aware_retriever(
                llm_without_streaming,
                retriever,
                self.get_condense_question_prompt(
                    custom_prompt=system_prompts.get("condenseSystemPrompt")
                ),
            )
            question_answer_chain = create_stuff_documents_chain(
                self.llm,
                self.get_qa_prompt(custom_prompt=system_prompts.get("systemPromptRag")),
            )
            chain = create_retrieval_chain(
                history_aware_retriever, question_answer_chain
            )
        else:
            chain = (
                self.get_prompt(custom_prompt=system_prompts.get("systemPrompt"))
                | self.llm
            )

        conversation = RunnableWithMessageHistory(
            chain,
            lambda session_id: self.chat_history,
            history_messages_key="chat_history",
            input_messages_key="input",
            output_messages_key="output",
        )

        config = {"configurable": {"session_id": self.session_id}}
        self.add_files_to_message_history(images, sessions_documents, videos)
        try:
            if (
                not self.disable_streaming
                and not self.should_call_apply_bedrock_guardrails()
                and self.model_kwargs.get("streaming", False)
            ):
                answer = ""
                for chunk in conversation.stream(
                    input={"input": user_prompt}, config=config
                ):
                    logger.debug("chunk", chunk=chunk)
                    if "answer" in chunk:
                        answer = answer + chunk["answer"]
                    elif isinstance(chunk, AIMessageChunk):
                        for c in chunk.content:
                            if "text" in c:
                                answer = answer + c.get("text")
            else:
                response = conversation.invoke(
                    input={"input": user_prompt}, config=config
                )
                if "answer" in response:
                    answer = response.get("answer")  # RAG flow
                else:
                    answer = response.content
        except Exception as e:
            logger.exception(e)
            raise e

        if workspace_id:
            # In the RAG flow, the history is not updated automatically
            self.chat_history.add_message(HumanMessage(user_prompt))
            self.chat_history.add_message(AIMessage(answer))
        if retriever is not None:
            workspace_documents = [
                {
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc in retriever.get_last_search_documents()
            ]

        # A3: citations are built ONLY from workspace_documents — the
        # ORIGINAL, unwrapped documents from retriever.get_last_search_documents()
        # (see SanitizingWorkspaceRetriever's docstring) — never from the
        # model's generated `answer` text and never from the
        # sanitized/wrapped copies that were handed to the LLM. This is
        # what makes a citation impossible for the model to fabricate:
        # it is derived purely from what was actually retrieved.
        citations = genai_core.citations.build_safe_citations(workspace_documents)

        clean_prompts = []
        for prompt in self.callback_handler.prompts:
            # Remove JSON from the promt (which contains binary of input files)
            clean_prompts.append(re.sub(r"\[{.*}\]*", "*FILE*", prompt))

        metadata = {
            "modelId": self.model_id,
            "modelKwargs": self.model_kwargs,
            "mode": self._mode,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "documents": workspace_documents,
            "prompts": clean_prompts,
        }

        if is_admin_role(user_groups):
            # Admins keep receiving everything they always have
            # (unchanged), plus the same safe `citations` that regular
            # users get.
            self.chat_history.add_metadata(
                {
                    **metadata,
                    "images": images,
                    "videos": videos,
                    "documents": sessions_documents + workspace_documents,
                    "citations": citations,
                }
            )
        elif citations:
            # A3: regular (non-admin) users get ONLY the safe, allow-
            # listed citations persisted — never modelId, modelKwargs,
            # raw prompts, or unredacted document metadata. Skipped
            # entirely when there are no citations (e.g. non-RAG chat),
            # to avoid writing an unnecessary empty record and to keep
            # today's non-RAG behavior identical.
            self.chat_history.add_metadata({"citations": citations})

        if (
            self.callback_handler.usage is not None
            and "total_tokens" in self.callback_handler.usage
        ):
            # Used by Cloudwatch filters to generate a metric of token usage.
            logger.info(
                "Usage Metric",
                # Each unique value of model id will create a
                # new cloudwatch metric (each one has a cost)
                model=self.model_id,
                metric_type="token_usage",
                value=self.callback_handler.usage.get("total_tokens"),
            )

        # A4: input/output token split + estimated cost. Reuses the same
        # real, provider-returned usage data as the pre-existing
        # "Usage Metric" line above (self.callback_handler.usage) —
        # never invents a token count. See genai_core.observability for
        # the metric-emission and pricing-lookup logic; both skip
        # emitting entirely when the underlying data is unavailable
        # (missing usage info, or an unrecognized/unverified-for-cost
        # model) rather than emitting a fabricated value.
        self._emit_token_and_cost_metrics()

        response = {
            "sessionId": self.session_id,
            "type": "text",
            "content": answer,
            "citations": citations,
        }

        if is_admin_role(user_groups) and metadata is not None:
            response["metadata"] = metadata
        else:
            response["metadata"] = {
                "sessionId": self.session_id,
            }

        return response

    def run_with_chain(
        self,
        user_prompt,
        workspace_id=None,
        user_groups=None,
        system_prompts={},
    ):
        if not self.llm:
            raise ValueError("llm must be set")

        self.callback_handler.prompts = []

        if workspace_id:
            retriever = SanitizingWorkspaceRetriever(workspace_id=workspace_id)
            conversation = ConversationalRetrievalChain.from_llm(
                self.llm,
                retriever,
                condense_question_llm=self.get_llm({"streaming": False}),
                condense_question_prompt=self.get_condense_question_prompt(
                    custom_prompt=system_prompts.get("condenseSystemPrompt")
                ),
                combine_docs_chain_kwargs={
                    "prompt": self.get_qa_prompt(
                        custom_prompt=system_prompts.get("systemPromptRag")
                    )
                },
                return_source_documents=True,
                memory=self.get_memory(output_key="answer", return_messages=True),
                verbose=True,
                callbacks=[self.callback_handler],
            )
            result = conversation({"question": user_prompt})
            logger.debug(result["source_documents"])
            # A2.1: use retriever.get_last_search_documents() (the
            # ORIGINAL, unwrapped documents) for citation metadata, not
            # result["source_documents"] directly — the latter now holds
            # the sanitized/delimited copies that SanitizingWorkspaceRetriever
            # returns to the langchain chain (see
            # genai_core/langchain/workspace_retriever.py). Citation
            # metadata should show real document text, not our internal
            # prompt-hardening markup.
            documents = [
                {
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc in retriever.get_last_search_documents()
            ]

            # A3: same rule as run_with_chain_v2 — citations are built
            # only from these original documents, never from
            # result["answer"] or the sanitized copies used in the
            # chain's actual prompt.
            citations = genai_core.citations.build_safe_citations(documents)

            metadata = {
                "modelId": self.model_id,
                "modelKwargs": self.model_kwargs,
                "mode": self._mode,
                "sessionId": self.session_id,
                "userId": self.user_id,
                "workspaceId": workspace_id,
                "documents": documents,
                "prompts": self.callback_handler.prompts,
            }

            if is_admin_role(user_groups) and metadata is not None:
                self.chat_history.add_metadata({**metadata, "citations": citations})
            elif citations:
                self.chat_history.add_metadata({"citations": citations})

            self._emit_token_and_cost_metrics()

            response = {
                "sessionId": self.session_id,
                "type": "text",
                "content": result["answer"],
                "citations": citations,
            }

            if is_admin_role(user_groups) and metadata is not None:
                response["metadata"] = metadata
            else:
                response["metadata"] = {
                    "sessionId": self.session_id,
                }
            return response

        conversation = ConversationChain(
            llm=self.llm,
            prompt=self.get_prompt(custom_prompt=system_prompts.get("systemPrompt")),
            memory=self.get_memory(),
            verbose=True,
        )
        answer = conversation.predict(
            input=user_prompt, callbacks=[self.callback_handler]
        )

        metadata = {
            "modelId": self.model_id,
            "modelKwargs": self.model_kwargs,
            "mode": self._mode,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "documents": [],
            "prompts": self.callback_handler.prompts,
        }

        if is_admin_role(user_groups) and metadata is not None:
            self.chat_history.add_metadata(metadata)

        self._emit_token_and_cost_metrics()

        response = {
            "sessionId": self.session_id,
            "type": "text",
            "content": answer,
        }

        if is_admin_role(user_groups) and metadata is not None:
            response["metadata"] = metadata

        return response

    def run_with_media_generation_chain(
        self, prompt, user_groups=None, images=[], documents=[], videos=[]
    ):
        # Get chat history
        messages = self.chat_history.messages
        # Format prompt
        input = self.format_prompt(prompt, messages, images + videos)

        self.add_files_to_message_history(images, documents, videos)

        try:
            if self._mode == ChatbotMode.IMAGE_GENERATION.value:
                # Chain process for image generation
                ai_response = self.generate_image(input, files=images)
            elif self._mode == ChatbotMode.VIDEO_GENERATION.value:
                # Chain process for video generation
                ai_response = self.generate_video(input, files=images)
            else:
                raise ValueError(f"unknown media generation mode {self._mode}")
        except Exception as e:
            logger.exception(e)
            raise e

        # Add user files and mesage to chat history
        user_message_metadata = {
            "modelId": self.model_id,
            "modelKwargs": self.model_kwargs,
            "mode": self._mode,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "images": images,
            "documents": documents,
            "videos": videos,
        }
        self.chat_history.add_user_message(prompt)
        self.chat_history.add_metadata(user_message_metadata)

        # Add AI files and message to chat history
        ai_images = ai_response.get("images", [])
        ai_videos = ai_response.get("videos", [])

        ai_response_metadata = {
            "modelId": self.model_id,
            "modelKwargs": self.model_kwargs,
            "mode": self._mode,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "images": ai_images,
            "videos": ai_videos,
        }

        ai_text_response = ai_response.get("content", "")
        self.chat_history.add_ai_message(ai_text_response)

        if is_admin_role(user_groups):
            ai_response_metadata.update(
                {
                    "prompts": [input.get("last_message")],
                }
            )
        self.chat_history.add_metadata(ai_response_metadata)

        response = {
            "sessionId": self.session_id,
            "type": "text",
            "content": ai_response.get("content"),
            "metadata": ai_response_metadata,
        }

        return response

    def run(
        self,
        prompt,
        workspace_id=None,
        images=[],
        documents=[],
        videos=[],
        user_groups=[],
        system_prompts={},
        *args,
        **kwargs,
    ):
        """A4: thin timing/metrics wrapper around _run_dispatch(), which
        contains the exact, unmodified dispatch logic that used to live
        directly in run(). This is the single choke point every chatbot
        mode (chain, RAG, image/video generation) passes through, so
        AIRequests/AIFailures/AIRequestLatency are emitted exactly once
        per top-level request regardless of mode, without duplicating
        this wrapping in every mode-specific method.

        On failure, the exception is re-raised after logging — this
        preserves the EXACT existing error-propagation behavior (the
        caller, lib/model-interfaces/.../request-handler/index.py's
        handle_run(), had no try/except around model.run() before this
        change and still doesn't observe any different behavior; a
        failure metric is purely an added side effect).
        """
        start_time = time.monotonic()
        try:
            response = self._run_dispatch(
                prompt,
                workspace_id,
                images,
                documents,
                videos,
                user_groups,
                system_prompts,
                *args,
                **kwargs,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            ai_metrics.log_ai_request_failure(
                model_id=self.model_id,
                mode=self._mode,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
            )
            raise

        latency_ms = (time.monotonic() - start_time) * 1000
        ai_metrics.log_ai_request_success(
            model_id=self.model_id, mode=self._mode, latency_ms=latency_ms
        )
        return response

    def _run_dispatch(
        self,
        prompt,
        workspace_id=None,
        images=[],
        documents=[],
        videos=[],
        user_groups=[],
        system_prompts={},
        *args,
        **kwargs,
    ):
        logger.debug(f"run with {kwargs}")
        logger.debug(f"workspace_id {workspace_id}")
        logger.debug(f"mode: {self._mode}")

        guardrail_response = self.apply_bedrock_guardrails(
            source="INPUT", content=prompt
        )
        if guardrail_response is not None:
            logger.info("Blocking intput message using Guardrails")
            return guardrail_response

        if self._mode == ChatbotMode.CHAIN.value:
            if isinstance(self.llm, ChatBedrockConverse):
                response = self.run_with_chain_v2(
                    prompt,
                    workspace_id,
                    images,
                    documents,
                    videos,
                    user_groups,
                    system_prompts=system_prompts,
                )
            else:
                response = self.run_with_chain(
                    prompt,
                    workspace_id,
                    user_groups,
                    system_prompts=system_prompts,
                )
            guardrail_response = self.apply_bedrock_guardrails(
                source="OUTPUT", content=response.get("content")
            )
            if guardrail_response is not None:
                # Replace the last message in the history
                logger.info("Blocking ouput message using Guardrails")
                self.chat_history.replace_last_message(
                    guardrail_response.get("content")
                )
                return guardrail_response

            return response

        elif self._mode in [
            ChatbotMode.IMAGE_GENERATION.value,
            ChatbotMode.VIDEO_GENERATION.value,
        ]:
            # Media generation
            return self.run_with_media_generation_chain(
                prompt,
                user_groups,
                images,
                documents,
                videos,
            )

        raise ValueError(f"unknown mode {self._mode}")


def is_admin_role(user_groups):
    if user_groups and ("admin" in user_groups or "workspace_manager" in user_groups):
        return True
    return False
