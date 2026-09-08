# AWS GenAI LLM Chatbot

Enterprise-oriented generative AI chatbot with RAG capabilities.

## Overview

The AWS GenAI LLM Chatbot is a solution that enables organizations to
deploy a chatbot powered by large language models (LLMs) with Retrieval
Augmented Generation (RAG) capabilities. This repository is based on
[aws-samples/aws-genai-llm-chatbot](https://github.com/aws-samples/aws-genai-llm-chatbot)
(the original AWS sample — see the `License` section below); it has not
been deployed or verified in a live AWS account as part of this
repository's A1–A6 work, so it is described here as an implemented,
locally-tested solution rather than a "production-ready" one. See
`FINAL_AUDIT.md` for the exact, current verification status.

### About the work in this repository

The original AWS sample (frontend, CDK infrastructure, multi-provider
model adapters, multi-engine RAG, Cognito auth, WAF, and more — see
`AUDIT.md` for a full inventory) was authored by Amazon.com, Inc. or its
affiliates and is used here unmodified except where noted. On top of
that foundation, this repository adds:

- **A1** — a standard-library-only RAG evaluation framework (`evaluation/`)
- **A2 / A2.1** — prompt-injection and RAG-poisoning defenses
  (`genai_core/security/prompt_guard.py`) integrated into the real
  retrieval path
- **A3** — redacted citation exposure to all authenticated users
  (`genai_core/citations.py`, GraphQL `Citation` type)
- **A4** — AI-specific observability: token/cost/latency metrics
  (`genai_core/observability/`)
- **A5** — upload/file security: server-side content-signature
  verification and zip-bomb heuristics (`genai_core/security/file_validation.py`)
  wired into the real upload-handler Lambda
- **A6** — CodeQL + Dependabot: static-analysis and dependency-security
  automation (`.github/workflows/codeql.yml`, `.github/dependabot.yml`)

See `ARCHITECTURE.md`, `SECURITY.md`, `OBSERVABILITY.md`, and
`FINAL_AUDIT.md` for full detail on what's implemented versus what
remains AWS-dependent and unverified.

**Testing evidence**: 211/211 tests passing across A1–A6 (locally run in
this repository's development sandbox — see `FINAL_AUDIT.md` for the
exact per-phase breakdown and what remains unverified pending a real
AWS account / GitHub Actions execution).

## Key Features

- **Multiple LLM Support**: Amazon Bedrock (Claude, Llama 2), SageMaker, and custom model endpoints
- **GenAIEH Gateway Integration**: Connect to GenAIEH Gateway for additional model access
- **Comprehensive RAG Implementation**: Connect to various data sources for context-aware responses
- **Enterprise Security**: Fine-grained access controls, audit logging, and data encryption
- **Conversation Memory**: Full conversation history with persistent storage
- **Web UI and API Access**: Modern React interface and API endpoints for integration
- **Cost Optimization**: Token usage tracking and cost management features
- **Deployment Flexibility**: Multiple deployment options to fit your needs

## Getting Started

This blueprint deploys the complete AWS GenAI LLM Chatbot solution in your AWS account.

### Prerequisites

- AWS Account with appropriate permissions
- AWS CLI configured with credentials
- Node.js 18+ and npm
- Python 3.8+
- AWS CDK CLI version compatible with aws-cdk-lib 2.206.0 or later
  ```bash
  # Install or update the CDK CLI globally
  npm install -g aws-cdk@latest
  
  # Verify the installed version
  cdk --version
  ```

> **Important**: The CDK CLI version must be compatible with the aws-cdk-lib version used in this project (currently 2.206.0). If you encounter a "Cloud assembly schema version mismatch" error during deployment, update your CDK CLI to the latest version using the command above.

### Deployment

The deployment process is fully automated using AWS CDK and SeedFarmer.

## Architecture

The solution architecture includes:

- Amazon Bedrock for LLM access
- Amazon OpenSearch for vector storage
- Amazon S3 for document storage
- Amazon Cognito for authentication
- AWS Lambda for serverless processing
- Amazon AppSync (GraphQL) for API access
- React-based web interface

## Documentation

For complete documentation, visit the [GitHub repository](https://github.com/aws-samples/aws-genai-llm-chatbot).

## License

This project is licensed under the MIT-0 License.
