import * as cdk from 'aws-cdk-lib';

export interface StageConfig {
  stage: 'alpha' | 'beta' | 'gamma' | 'prod';
  account: string;
  region: string;
  removalPolicy: cdk.RemovalPolicy;
  lambdaMemoryMB: number;
  lambdaTimeoutSeconds: number;
  cacheMaxSize: number;
  cacheTtlSeconds: number;
  amsEndpointUrl: string;
  alarmEmail: string;
  enableXRay: boolean;
  logLevel: 'DEBUG' | 'INFO' | 'WARNING';
}

export const STAGES: Record<string, StageConfig> = {
  alpha: {
    stage: 'alpha',
    account: process.env.CDK_DEFAULT_ACCOUNT ?? '123456789012',
    region: 'us-east-1',
    removalPolicy: cdk.RemovalPolicy.DESTROY,
    lambdaMemoryMB: 256,
    lambdaTimeoutSeconds: 10,
    cacheMaxSize: 64,
    cacheTtlSeconds: 60,
    amsEndpointUrl: 'https://pi5ywcu3ub.execute-api.us-east-1.amazonaws.com/alpha',
    alarmEmail: 'avadhani.a@northeastern.edu',
    enableXRay: true,
    logLevel: 'DEBUG',
  },
  beta: {
    stage: 'beta',
    account: process.env.CDK_DEFAULT_ACCOUNT ?? '234567890123',
    region: 'us-east-1',
    removalPolicy: cdk.RemovalPolicy.DESTROY,
    lambdaMemoryMB: 512,
    lambdaTimeoutSeconds: 10,
    cacheMaxSize: 128,
    cacheTtlSeconds: 60,
    amsEndpointUrl: 'https://beta-ams-placeholder.execute-api.us-east-1.amazonaws.com/beta',
    alarmEmail: 'avadhani.a@northeastern.edu',
    enableXRay: true,
    logLevel: 'DEBUG',
  },
  gamma: {
    stage: 'gamma',
    account: process.env.CDK_DEFAULT_ACCOUNT ?? '345678901234',
    region: 'us-east-1',
    removalPolicy: cdk.RemovalPolicy.DESTROY,
    lambdaMemoryMB: 512,
    lambdaTimeoutSeconds: 10,
    cacheMaxSize: 256,
    cacheTtlSeconds: 60,
    amsEndpointUrl: 'https://idco76hrk9.execute-api.us-east-1.amazonaws.com/gamma',
    alarmEmail: 'avadhani.a@northeastern.edu',
    enableXRay: true,
    logLevel: 'INFO',
  },
  prod: {
    stage: 'prod',
    account: process.env.CDK_DEFAULT_ACCOUNT ?? '456789012345',
    region: 'us-east-1',
    removalPolicy: cdk.RemovalPolicy.RETAIN,
    lambdaMemoryMB: 512,
    lambdaTimeoutSeconds: 10,
    cacheMaxSize: 256,
    cacheTtlSeconds: 60,
    amsEndpointUrl: 'https://afwtpvnxe7.execute-api.us-east-1.amazonaws.com/prod',
    alarmEmail: 'avadhani.a@northeastern.edu',
    enableXRay: true,
    logLevel: 'INFO',
  },
};
