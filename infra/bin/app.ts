#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';

const app = new cdk.App();

// 리전은 파라미터로 지정 가능 — 기본은 서울(ap-northeast-2). 우선순위:
//   CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(=`cdk deploy`가 프로파일에서 채워줌) > 서울.
// (도쿄는 Lambda MicroVMs 때문에 강제됐던 것 — VM이 사라졌으니 더는 필요 없다.)
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';

new PathfinderDrillStack(app, 'PathfinderDrillStack', {
  env: { region, account: process.env.CDK_DEFAULT_ACCOUNT },
});
