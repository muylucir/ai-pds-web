#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';

const app = new cdk.App();
new PathfinderDrillStack(app, 'PathfinderDrillStack', {
  env: { region: 'ap-northeast-1', account: process.env.CDK_DEFAULT_ACCOUNT },
});
