#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

const drill = new PathfinderDrillStack(app, 'PathfinderDrillStack', { env });

// 인증 스택: User Pool · 그룹 · Hosted UI v2 · 앱 클라이언트 · 시드 계정 2개.
// 콜백 URL은 localhost만 갖고 배포되며, 실제 CloudFront 도메인은 아래 호스팅
// 스택이 UpdateUserPoolClient로 덧붙인다(순환 의존 해소).
const auth = new PathfinderAuthStack(app, 'PathfinderAuthStack', { env });

// 호스팅 스택은 CloudFront origin-facing 프리픽스 리스트를 배포 리전에서
// 자동 조회한다(fromLookup) — synth/deploy 시 크리덴셜 필요, 결과는
// cdk.context.json에 캐시된다(커밋 대상).
new PathfinderHostingStack(app, 'PathfinderHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  hostedUiDomain: auth.hostedUiDomain,
});
