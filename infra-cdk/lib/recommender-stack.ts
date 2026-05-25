import * as path from 'path';
import * as childProcess from 'child_process';
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import { Construct } from 'constructs';
import { StageConfig } from './stage-config';

export interface RecommenderStackProps extends cdk.StackProps {
  config: StageConfig;
  /** Override the Lambda code asset — used in tests to avoid Docker bundling. */
  lambdaCode?: lambda.Code;
}

export class RecommenderStack extends cdk.Stack {
  public readonly apiUrl: string;

  constructor(scope: Construct, id: string, props: RecommenderStackProps) {
    super(scope, id, props);
    const { config } = props;

    const lambdaDir = path.join(__dirname, '../../lambda');
    const defaultCode = lambda.Code.fromAsset(lambdaDir, {
      bundling: {
        // Local bundler runs pip natively — no Docker required on dev machines.
        // CDK falls back to Docker if pip is unavailable (e.g. raw CI agents).
        local: {
          tryBundle(outputDir: string): boolean {
            try {
              childProcess.execSync(
                `pip install -r requirements.txt -t "${outputDir}" --quiet && cp -r . "${outputDir}"`,
                { cwd: lambdaDir, stdio: 'inherit' },
              );
              return true;
            } catch {
              return false;
            }
          },
        },
        image: lambda.Runtime.PYTHON_3_11.bundlingImage,
        command: [
          'bash',
          '-c',
          'pip install -r requirements.txt -t /asset-output && cp -r . /asset-output',
        ],
      },
    });

    const handler = new lambda.Function(this, 'RecommenderHandler', {
      functionName: `version-recommender-handler-${config.stage}`,
      runtime: lambda.Runtime.PYTHON_3_11,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.lambda_handler',
      code: props.lambdaCode ?? defaultCode,
      memorySize: config.lambdaMemoryMB,
      timeout: cdk.Duration.seconds(config.lambdaTimeoutSeconds),
      tracing: config.enableXRay ? lambda.Tracing.ACTIVE : lambda.Tracing.DISABLED,
      environment: {
        AMS_ENDPOINT_URL: config.amsEndpointUrl,
        STAGE: config.stage,
        CACHE_TTL_SECONDS: String(config.cacheTtlSeconds),
        CACHE_MAX_SIZE: String(config.cacheMaxSize),
        LOG_LEVEL: config.logLevel,
        LAMBDA_PACKAGE_VERSION: process.env.BUILD_VERSION ?? 'dev',
        POWERTOOLS_SERVICE_NAME: 'version-recommender',
      },
    });

    const api = new apigateway.RestApi(this, 'RecommenderApi', {
      restApiName: `version-recommender-api-${config.stage}`,
      deployOptions: {
        stageName: config.stage,
        dataTraceEnabled: false,
        throttlingBurstLimit: 200,
        throttlingRateLimit: 100,
      },
      defaultMethodOptions: {
        authorizationType: apigateway.AuthorizationType.IAM,
      },
    });

    const integration = new apigateway.LambdaIntegration(handler);

    // GET /health — no auth
    const health = api.root.addResource('health');
    health.addMethod('GET', integration, {
      authorizationType: apigateway.AuthorizationType.NONE,
    });

    // POST /recommend/{modelName} — AWS_IAM
    const recommend = api.root.addResource('recommend');
    const recommendModel = recommend.addResource('{modelName}');
    recommendModel.addMethod('POST', integration);

    this.apiUrl = api.url;

    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
  }
}
