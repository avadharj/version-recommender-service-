import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Template } from 'aws-cdk-lib/assertions';
import { RecommenderStack } from '../lib/recommender-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { STAGES } from '../lib/stage-config';

// Stable stub asset — avoids Docker bundling in tests.
// A fresh Code instance is required per stack (CDK enforces one stack per asset).
const stubCode = () =>
  lambda.Code.fromAsset(path.join(__dirname, 'fixtures/stub-handler.zip'));

const stageNames = ['alpha', 'beta', 'gamma', 'prod'] as const;

for (const stageName of stageNames) {
  const config = STAGES[stageName];
  const env = { account: config.account, region: config.region };

  describe(`RecommenderStack ${stageName}`, () => {
    const app = new cdk.App();
    const stack = new RecommenderStack(app, `TestRecommenderStack-${stageName}`, {
      config,
      env,
      lambdaCode: stubCode(),
    });

    it('matches snapshot', () => {
      expect(Template.fromStack(stack).toJSON()).toMatchSnapshot();
    });

    it('has Python 3.11 ARM64 Lambda', () => {
      Template.fromStack(stack).hasResourceProperties('AWS::Lambda::Function', {
        Runtime: 'python3.11',
        Architectures: ['arm64'],
      });
    });

    it('sets AMS_ENDPOINT_URL from stage config', () => {
      Template.fromStack(stack).hasResourceProperties('AWS::Lambda::Function', {
        Environment: {
          Variables: {
            AMS_ENDPOINT_URL: config.amsEndpointUrl,
            STAGE: stageName,
          },
        },
      });
    });

    it('has no SnapStart', () => {
      // Python Lambda does not support SnapStart. Verify the property is absent.
      const functions = Template.fromStack(stack).findResources('AWS::Lambda::Function');
      const fnValues = Object.values(functions);
      fnValues.forEach((fn: any) => {
        expect(fn.Properties.SnapStart).toBeUndefined();
      });
    });
  });

  describe(`ObservabilityStack ${stageName}`, () => {
    const app = new cdk.App();
    const stack = new ObservabilityStack(app, `TestObservabilityStack-${stageName}`, {
      config,
      env,
    });

    it('matches snapshot', () => {
      expect(Template.fromStack(stack).toJSON()).toMatchSnapshot();
    });
  });
}
