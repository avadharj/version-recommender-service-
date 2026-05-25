#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { RecommenderStack } from '../lib/recommender-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { STAGES } from '../lib/stage-config';

const app = new cdk.App();

const stageName = app.node.tryGetContext('stage') as string;
if (!stageName) {
  throw new Error('Stage context required. Pass -c stage=<alpha|beta|gamma|prod>');
}

const config = STAGES[stageName];
if (!config) {
  throw new Error(
    `Unknown stage: "${stageName}". Valid stages: ${Object.keys(STAGES).join(', ')}`,
  );
}

const env = { account: config.account, region: config.region };

const recommenderStack = new RecommenderStack(app, `VersionRecommender-${stageName}`, {
  config,
  env,
});

const observabilityStack = new ObservabilityStack(
  app,
  `VersionRecommender-Observability-${stageName}`,
  { config, env },
);

[recommenderStack, observabilityStack].forEach((stack) => {
  cdk.Tags.of(stack).add('Stage', stageName);
  cdk.Tags.of(stack).add('Service', 'VersionRecommender');
  cdk.Tags.of(stack).add('Owner', 'arjun');
});
