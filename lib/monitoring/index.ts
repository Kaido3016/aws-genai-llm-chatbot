import { Duration, Stack } from "aws-cdk-lib";
import { IGraphqlApi } from "aws-cdk-lib/aws-appsync";
import {
  LogQueryWidget,
  MathExpression,
  Metric,
} from "aws-cdk-lib/aws-cloudwatch";
import { ITable } from "aws-cdk-lib/aws-dynamodb";
import { IFunction as ILambdaFunction } from "aws-cdk-lib/aws-lambda";
import { CfnCollection } from "aws-cdk-lib/aws-opensearchserverless";
import { Bucket } from "aws-cdk-lib/aws-s3";
import { IStateMachine } from "aws-cdk-lib/aws-stepfunctions";
import {
  AxisPosition,
  MonitoringFacade,
  SingleWidgetDashboardSegment,
  SnsAlarmActionStrategy,
} from "cdk-monitoring-constructs";
import { Construct } from "constructs";
import { CfnIndex } from "aws-cdk-lib/aws-kendra";
import { IDatabaseCluster } from "aws-cdk-lib/aws-rds";
import { Queue } from "aws-cdk-lib/aws-sqs";
import { FilterPattern, ILogGroup, MetricFilter } from "aws-cdk-lib/aws-logs";
import { IDistribution } from "aws-cdk-lib/aws-cloudfront";
import { ITopic, Topic } from "aws-cdk-lib/aws-sns";
import { NagSuppressions } from "cdk-nag";
import { Shared } from "../shared";
import { PolicyStatement, ServicePrincipal } from "aws-cdk-lib/aws-iam";

export interface MonitoringProps {
  prefix: string;
  readonly shared: Shared;
  advancedMonitoring: boolean;
  appsycnApi: IGraphqlApi;
  appsyncResolversLogGroups: ILogGroup[];
  llmRequestHandlersLogGroups: ILogGroup[];
  cognito: { userPoolId: string; clientId: string };
  tables: ITable[];
  buckets: Bucket[];
  sqs: Queue[];
  ragFunctionProcessing: ILambdaFunction[];
  ragImportStateMachineProcessing: IStateMachine[];
  ragEngineStateMachineProcessing: (IStateMachine | undefined)[];
  aurora?: IDatabaseCluster;
  opensearch?: CfnCollection;
  kendra?: CfnIndex;
  cloudFrontDistribution?: IDistribution;
}

export class Monitoring extends Construct {
  public readonly compositeAlarmTopic?: ITopic;

  constructor(
    private scope: Construct,
    id: string,
    props: MonitoringProps
  ) {
    super(scope, id);

    const monitoring = new MonitoringFacade(
      this,
      props.prefix + "GenAI-Chatbot-Dashboard",
      {
        metricFactoryDefaults: {
          period: Duration.minutes(5),
        },
      }
    );

    const region = Stack.of(scope).region;

    monitoring.addLargeHeader("APIs").monitorAppSyncApi({
      api: props.appsycnApi,
      alarmFriendlyName: "AppSync",
      ...(props.advancedMonitoring
        ? {
            add5XXFaultCountAlarm: {
              Critical: {
                // 5 error during the default period
                maxErrorCount: 5,
              },
            },
            addLatencyP90Alarm: {
              Critical: {
                maxLatency: Duration.seconds(3),
              },
            },
          }
        : {}),
    });

    monitoring.addSegment(
      new SingleWidgetDashboardSegment(
        this.getLogsWidget("Resolvers Logs:", props.appsyncResolversLogGroups, [
          "identify.claims.sub as cognito_user",
          "correlation_id",
        ])
      )
    );
    // Only add LLM Request Handlers Logs widget if there are log groups
    if (props.llmRequestHandlersLogGroups.length > 0) {
      monitoring.addSegment(
        new SingleWidgetDashboardSegment(
          this.getLogsWidget(
            "LLM Request Handlers Logs:",
            props.llmRequestHandlersLogGroups,
            []
          )
        )
      );
    }

    if (props.advancedMonitoring) {
      this.addMetricFilter(
        props.prefix + "GenAI",
        monitoring,
        props.llmRequestHandlersLogGroups
      );
      // A4: AI-specific observability (token/cost/latency/RAG metrics).
      // Reuses the same log groups and the same "advancedMonitoring"
      // opt-in flag as the pre-existing TokenUsage filter above, since
      // these new metrics come from structured log lines emitted by the
      // same Lambda functions.
      this.addAIObservabilityMetricFilters(
        props.prefix + "GenAI",
        monitoring,
        props.llmRequestHandlersLogGroups
      );
    }

    const link = `https://${region}.console.aws.amazon.com/cognito/v2/idp/user-pools/${props.cognito.userPoolId}/users?region=${region}`;
    const title = `Cognito [**UserPool**](${link})`;
    this.addCognitoMetrics(
      monitoring,
      props.cognito.userPoolId,
      props.cognito.clientId,
      title
    );

    if (props.cloudFrontDistribution) {
      monitoring.addLargeHeader("Front End").monitorCloudFrontDistribution({
        distribution: props.cloudFrontDistribution,
        alarmFriendlyName: "CloudFront",
        ...(props.advancedMonitoring
          ? {
              addFault5xxRate: {
                Critical: {
                  maxErrorRate: 0.1,
                },
              },
            }
          : {}),
      });
    }

    monitoring.addLargeHeader("Storage");

    for (const table of props.tables) {
      monitoring.monitorDynamoTable({
        table,
        alarmFriendlyName: table.node.id,
        humanReadableName: table.node.id,
      });
    }

    for (const bucket of props.buckets) {
      monitoring.monitorS3Bucket({
        bucket,
        alarmFriendlyName: bucket.node.id,
        humanReadableName: bucket.node.id,
      });
    }

    if (props.aurora) {
      monitoring.monitorAuroraCluster({
        cluster: props.aurora,
        alarmFriendlyName: props.aurora.node.id,
        humanReadableName: props.aurora.node.id,
      });
    }

    if (props.opensearch) {
      const link = `https://${region}.console.aws.amazon.com/aos/home?region=${region}#opensearch/collections/${props.opensearch.name}`;
      const title = `OpenSearch [**${props.opensearch.node.id}**](${link})`;
      this.addOpenSearchMetrics(monitoring, props.opensearch, title);
    }

    if (props.kendra) {
      const link = `https://${region}.console.aws.amazon.com/kendra/home?region=${region}#indexes/${props.kendra.ref}`;
      const title = `Kendra [**${props.kendra.node.id}**](${link})`;
      this.addKendraMonitoring(monitoring, props.kendra, title);
    }

    monitoring.addLargeHeader("Requests Processing");
    for (const queue of props.sqs) {
      if (!queue.deadLetterQueue) {
        throw new Error("Please create a DLQ for " + queue.node.id);
      }
      monitoring.monitorSqsQueueWithDlq({
        queue: queue,
        deadLetterQueue: queue.deadLetterQueue.queue,
        alarmFriendlyName: queue.node.id,
        humanReadableName: queue.node.id,
        ...(props.advancedMonitoring
          ? {
              addDeadLetterQueueMaxSizeAlarm: {
                Critical: {
                  // If 5 messages are in the dead letter queue
                  // if means 5 requests where not processed after retries.
                  maxMessageCount: 5,
                },
              },
            }
          : {}),
      });
    }
    monitoring.addLargeHeader("RAG processing (File or website import)");
    for (const fct of props.ragImportStateMachineProcessing) {
      monitoring.monitorStepFunction({
        stateMachine: fct,
        humanReadableName: fct.node.id,
        alarmFriendlyName: fct.node.id,
        ...(props.advancedMonitoring
          ? {
              addFailedExecutionRateAlarm: {
                Critical: {
                  // If 10% of the imports are failing
                  maxErrorRate: 0.1,
                },
              },
            }
          : {}),
      });
    }
    monitoring.addLargeHeader(
      "RAG engines management (Create table/index and cleanup)"
    );
    for (const fct of props.ragEngineStateMachineProcessing) {
      if (fct) {
        monitoring.monitorStepFunction({
          stateMachine: fct,
          humanReadableName: fct.node.id,
          alarmFriendlyName: fct.node.id,
          ...(props.advancedMonitoring
            ? {
                addFailedExecutionCountAlarm: {
                  Critical: {
                    // If any process fail, cause an alarm (create an OpenSearch index for example)
                    maxErrorCount: 0,
                    datapointsToAlarm: 1,
                  },
                },
              }
            : {}),
        });
      }
    }
    for (const fct of props.ragFunctionProcessing) {
      monitoring.monitorLambdaFunction({
        lambdaFunction: fct,
        humanReadableName: fct.node.id,
        alarmFriendlyName: fct.node.id,
        ...(props.advancedMonitoring
          ? {
              addFaultRateAlarm: {
                Critical: {
                  // If 10% of the imports are failing
                  maxErrorRate: 0.1,
                },
              },
            }
          : {}),
      });
    }

    if (props.advancedMonitoring) {
      const onAlarmTopic = new Topic(this, "CompositeAlarmTopic", {
        displayName: props.prefix + "CompositeAlarmTopic",
        enforceSSL: true,
        masterKey: props.shared.kmsKey,
      });

      if (props.shared.kmsKey) {
        // Following the guide
        // https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html#sns-what-permissions-for-sse
        props.shared.kmsKey.addToResourcePolicy(
          new PolicyStatement({
            actions: ["kms:GenerateDataKey*", "kms:Decrypt"],
            principals: [new ServicePrincipal("cloudwatch.amazonaws.com")],
            resources: ["*"],
          })
        );
      }

      /**
       * CDK NAG suppression
       */
      NagSuppressions.addResourceSuppressions(onAlarmTopic, [
        { id: "AwsSolutions-SNS2", reason: "No sensitive data in topic." },
        { id: "AwsSolutions-SNS3", reason: "No sensitive data in topic." },
      ]);

      monitoring.createCompositeAlarmUsingDisambiguator("Critical", {
        disambiguator: "Critical",
        actionOverride: new SnsAlarmActionStrategy({ onAlarmTopic }),
      });

      this.compositeAlarmTopic = onAlarmTopic;
    }
  }

  private addMetricFilter(
    namespace: string,
    monitoring: MonitoringFacade,
    logGroups: ILogGroup[]
  ) {
    for (const logGroupKey in logGroups) {
      new MetricFilter(this, "UsageFilter" + logGroupKey, {
        logGroup: logGroups[logGroupKey],
        metricNamespace: namespace,
        metricName: "TokenUsage",
        filterPattern: FilterPattern.stringValue(
          "$.metric_type",
          "=",
          "token_usage"
        ),
        metricValue: "$.value",
        dimensions: {
          model: "$.model",
        },
      });
    }

    monitoring.monitorCustom({
      alarmFriendlyName: "TokenUsage",
      humanReadableName: "Token Usage",
      metricGroups: [
        {
          title: "LLM Usage (Bedrock)",
          metrics: [
            {
              alarmFriendlyName: "TokenUsage",
              metric: new MathExpression({
                label: "Tokens",
                expression: `SEARCH('{${namespace},model} MetricName=TokenUsage', 'Sum', 60)`,
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "LLMChains Calls",
              metric: new MathExpression({
                label: "Calls",
                expression: `SEARCH('{${namespace},model} MetricName=TokenUsage', 'SampleCount', 60)`,
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
      ],
    });
  }

  /**
   * A4 — AI-specific observability: token usage, estimated cost,
   * request-level and RAG-retrieval latency, and success/failure counts.
   *
   * Follows the EXACT same mechanism as the pre-existing TokenUsage
   * filter above (CloudWatch Logs Metric Filters over structured
   * Powertools Logger output, matched by a `metric_type` JSON field) —
   * see genai_core.observability.ai_metrics for the Python side that
   * emits these structured log lines. This is a deliberate choice not
   * to introduce a second observability mechanism (e.g. Powertools
   * EMF/Metrics), which is not used anywhere else in this project.
   *
   * Dimensions are intentionally limited to `model`, `mode`, and
   * `engine` — small, fixed-cardinality values never derived from user
   * input, session/user/workspace/document identifiers, or content. See
   * genai_core/observability/ai_metrics.py's module docstring for the
   * corresponding guarantee on the emitting side.
   */
  private addAIObservabilityMetricFilters(
    namespace: string,
    monitoring: MonitoringFacade,
    logGroups: ILogGroup[]
  ) {
    // metric_type -> [CloudWatch metric name, extra dimensions beyond
    // the ones every entry gets]. Kept as a small, explicit table rather
    // than one-off blocks per metric, so adding a future metric_type is
    // a one-line change instead of a copy-pasted MetricFilter block.
    const logBasedMetrics: Array<{
      metricType: string;
      metricName: string;
      dimensions: Record<string, string>;
    }> = [
      {
        metricType: "input_tokens",
        metricName: "InputTokens",
        dimensions: { model: "$.model" },
      },
      {
        metricType: "output_tokens",
        metricName: "OutputTokens",
        dimensions: { model: "$.model" },
      },
      {
        metricType: "estimated_cost_usd",
        // Name makes clear in the CloudWatch console itself that this
        // is an estimate, not the actual AWS bill — see
        // genai_core/observability/pricing.py for the (partially
        // unverified) methodology behind this number.
        metricName: "EstimatedAICostUSD",
        dimensions: { model: "$.model" },
      },
      {
        metricType: "ai_request_success",
        metricName: "AIRequests",
        dimensions: { model: "$.model", mode: "$.mode" },
      },
      {
        metricType: "ai_request_failure",
        metricName: "AIFailures",
        dimensions: { model: "$.model", mode: "$.mode" },
      },
      {
        metricType: "ai_request_latency",
        metricName: "AIRequestLatency",
        dimensions: { model: "$.model", mode: "$.mode" },
      },
      {
        metricType: "rag_retrieval_latency",
        metricName: "RAGRetrievalLatency",
        // engine (aurora/opensearch/kendra/bedrock_kb) only — never the
        // workspace_id the retrieval was performed against.
        dimensions: { engine: "$.engine" },
      },
      {
        metricType: "rag_documents_retrieved",
        metricName: "RAGDocumentsRetrieved",
        dimensions: { engine: "$.engine" },
      },
    ];

    for (const { metricType, metricName, dimensions } of logBasedMetrics) {
      for (const logGroupKey in logGroups) {
        new MetricFilter(this, `AIObs${metricName}Filter${logGroupKey}`, {
          logGroup: logGroups[logGroupKey],
          metricNamespace: namespace,
          metricName,
          filterPattern: FilterPattern.stringValue(
            "$.metric_type",
            "=",
            metricType
          ),
          metricValue: "$.value",
          dimensions,
        });
      }
    }

    // Dashboard section — a concise set of operational widgets, not a
    // widget per metric_type/dimension combination, per the instruction
    // to avoid an oversized dashboard.
    monitoring.monitorCustom({
      alarmFriendlyName: "AIObservability",
      humanReadableName: "AI Observability (tokens, cost, latency, RAG)",
      metricGroups: [
        {
          title: "AI Requests (success vs. failure)",
          metrics: [
            {
              alarmFriendlyName: "AIRequests",
              metric: new MathExpression({
                label: "Requests (success)",
                expression: `SEARCH('{${namespace},model,mode} MetricName=AIRequests', 'Sum', 60)`,
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "AIFailures",
              metric: new MathExpression({
                label: "Requests (failed)",
                expression: `SEARCH('{${namespace},model,mode} MetricName=AIFailures', 'Sum', 60)`,
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
        {
          title: "AI Request Latency (ms)",
          metrics: [
            {
              alarmFriendlyName: "AIRequestLatency",
              metric: new MathExpression({
                label: "Latency (ms)",
                expression: `SEARCH('{${namespace},model,mode} MetricName=AIRequestLatency', 'Average', 60)`,
              }),
              addAlarm: {},
            },
          ],
        },
        {
          title: "Token Usage (input/output)",
          metrics: [
            {
              alarmFriendlyName: "InputTokens",
              metric: new MathExpression({
                label: "Input tokens",
                expression: `SEARCH('{${namespace},model} MetricName=InputTokens', 'Sum', 60)`,
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "OutputTokens",
              metric: new MathExpression({
                label: "Output tokens",
                expression: `SEARCH('{${namespace},model} MetricName=OutputTokens', 'Sum', 60)`,
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
        {
          title: "Estimated AI Cost (USD, NOT the actual AWS bill)",
          metrics: [
            {
              alarmFriendlyName: "EstimatedAICostUSD",
              metric: new MathExpression({
                label: "Estimated cost (USD)",
                expression: `SEARCH('{${namespace},model} MetricName=EstimatedAICostUSD', 'Sum', 60)`,
              }),
              addAlarm: {},
            },
          ],
        },
        {
          title: "RAG Retrieval Latency (ms) by engine",
          metrics: [
            {
              alarmFriendlyName: "RAGRetrievalLatency",
              metric: new MathExpression({
                label: "Retrieval latency (ms)",
                expression: `SEARCH('{${namespace},engine} MetricName=RAGRetrievalLatency', 'Average', 60)`,
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "RAGDocumentsRetrieved",
              metric: new MathExpression({
                label: "Documents retrieved",
                expression: `SEARCH('{${namespace},engine} MetricName=RAGDocumentsRetrieved', 'Average', 60)`,
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
      ],
    });
  }

  private addCognitoMetrics(
    monitoring: MonitoringFacade,
    userpoolId: string,
    clientId: string,
    title: string
  ): void {
    const namespace = "AWS/Cognito";
    const dimensionsMap = {
      UserPool: userpoolId,
      UserPoolClient: clientId,
    };
    monitoring.monitorCustom({
      alarmFriendlyName: "Cognito",
      humanReadableName: title,
      metricGroups: [
        {
          title: "SignIn",
          metrics: [
            {
              alarmFriendlyName: "SignInSuccesses",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "SignInSuccesses",
                statistic: "sum",
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "FederationSuccesses",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "FederationSuccesses",
                statistic: "sum",
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
      ],
    });
  }
  private addOpenSearchMetrics(
    monitoring: MonitoringFacade,
    collection: CfnCollection,
    title: string
  ): void {
    const namespace = "AWS/AOSS";
    const dimensionsMap = {
      CollectionId: collection.ref,
      ClientId: Stack.of(this.scope).account,
      CollectionName: collection.name,
    };
    monitoring.monitorCustom({
      alarmFriendlyName: "OpenSearch",
      humanReadableName: title,
      metricGroups: [
        {
          title: "Search",
          metrics: [
            {
              alarmFriendlyName: "SearchRequestRate",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "SearchRequestRate",
                statistic: "sum",
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "SearchRequestLatency",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "SearchRequestLatency",
                statistic: "avg",
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
        {
          title: "Errors",
          metrics: [
            {
              alarmFriendlyName: "IngestionRequestErrors",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "IngestionRequestErrors",
                statistic: "sum",
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "SearchRequestErrors",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "SearchRequestErrors",
                statistic: "sum",
              }),
              addAlarm: {},
            },
          ],
        },
      ],
    });
  }
  private addKendraMonitoring(
    monitoring: MonitoringFacade,
    kendra: CfnIndex,
    title: string
  ): void {
    const namespace = "AWS/Kendra";
    const dimensionsMap = {
      IndexId: kendra.ref,
    };
    monitoring.monitorCustom({
      alarmFriendlyName: "Kendra",
      humanReadableName: title,
      metricGroups: [
        {
          title: "Search",
          metrics: [
            {
              alarmFriendlyName: "IndexQueryCount",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "IndexQueryCount",
                statistic: "sum",
              }),
              addAlarm: {},
            },
            {
              alarmFriendlyName: "IndexDocumentCount",
              metric: new Metric({
                namespace,
                dimensionsMap,
                metricName: "IndexDocumentCount",
                statistic: "sum",
              }),
              addAlarm: {},
              position: AxisPosition.RIGHT,
            },
          ],
        },
      ],
    });
  }

  private getLogsWidget(
    title: string,
    logGroups: ILogGroup[],
    extraFields: string[]
  ): LogQueryWidget {
    // Log Query Results
    return new LogQueryWidget({
      logGroupNames: logGroups.map((i) => i.logGroupName),
      height: 5,
      width: 24, // Full width
      title: title,
      /**
       * https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html
       */
      queryLines: [
        "fields @timestamp, message, level, location" +
          (extraFields.length > 0 ? "," + extraFields.join(",") : ""),
        `filter ispresent(level)`, // only includes messages using the logger
        "sort @timestamp desc",
        `limit 200`,
      ],
    });
  }
}
