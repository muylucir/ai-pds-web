#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';
import { PathfinderVmStack } from '../lib/pathfinder-vm-stack';

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
// 프로토타입 빌드 VM 설정은 VmStack(Tokyo) 배포 후 그 출력값을 여기로
// 주입한다 — 크로스-리전이라 CDK 참조로는 연결되지 않는다:
//   npx cdk deploy PathfinderHostingStack \
//     -c vmImageId=<VmStack ImageArn> -c vmRoleArn=<VmStack ExecutionRoleArn>
// 미주입 시 프로토타입 빌드만 비활성(명확한 503), 나머지 앱은 정상.
new PathfinderHostingStack(app, 'PathfinderHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  vmImageId: app.node.tryGetContext('vmImageId'),
  vmRoleArn: app.node.tryGetContext('vmRoleArn'),
  vmRegion: app.node.tryGetContext('vmRegion') ?? 'ap-northeast-1',
});

// MicroVM 리소스는 Lambda MicroVMs 서비스가 존재하는 Tokyo에 고정 배포 —
// 위 region 변수(기본 서울)와 무관하게 항상 ap-northeast-1.
new PathfinderVmStack(app, 'PathfinderVmStack', {
  env: { region: 'ap-northeast-1', account },
});
