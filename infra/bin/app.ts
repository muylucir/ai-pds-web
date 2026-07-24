#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

const drill = new PathfinderDrillStack(app, 'PathfinderDrillStack', { env });

// 호스팅 스택은 CloudFront origin-facing 프리픽스 리스트를 배포 리전에서
// 자동 조회한다(fromLookup) — synth/deploy 시 크리덴셜 필요, 결과는
// cdk.context.json에 캐시된다(커밋 대상).
new PathfinderHostingStack(app, 'PathfinderHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
});
