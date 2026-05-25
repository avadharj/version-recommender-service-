import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { StageConfig } from './stage-config';

export interface ObservabilityStackProps extends cdk.StackProps {
  config: StageConfig;
}

// Alarms and dashboard implemented in Story 6.4.
export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
  }
}
