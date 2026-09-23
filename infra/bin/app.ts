#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { AipdsPreviewStack } from '../lib/aipds-preview-stack';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

// 브라우저가 아티팩트 버킷으로 직접 PUT할 출처(프로젝트 가져오기의 presigned
// 업로드). 실제 값은 호스팅 스택의 CloudFront 도메인인데 그 스택이 이미 이 버킷에
// 의존하므로 상호 참조가 순환이다 — 그래서 배포자가 값을 건넨다.
//
// 첫 배포에는 값이 없다(도메인이 아직 없다): localhost만 허용된 채 배포되고,
// AipdsHostingStack의 DistributionDomain 출력이 나온 뒤 그 값으로 이 스택만 다시
// 배포한다. **버킷 전용 스택이므로 EC2를 교체하지 않는다** — 프로토타입 데이터가
// 사라지는 재배포와 다르다.
//
//   AIPDS_UPLOAD_ORIGINS=https://d111111abcdef8.cloudfront.net npm run deploy -- AipdsDrillStack
const uploadOrigins = (process.env.AIPDS_UPLOAD_ORIGINS ?? '')
  .split(',')
  .map((o) => o.trim())
  .filter((o) => o.length > 0);

const drill = new AipdsDrillStack(app, 'AipdsDrillStack', { env, uploadOrigins });

// 인증 스택: User Pool · 그룹 · Hosted UI v2 · 앱 클라이언트 · 시드 계정 2개.
// 콜백 URL은 localhost만 갖고 배포되며, 실제 CloudFront 도메인은 아래 호스팅
// 스택이 UpdateUserPoolClient로 덧붙인다(순환 의존 해소).
const auth = new AipdsAuthStack(app, 'AipdsAuthStack', { env });

// 호스팅 스택은 CloudFront origin-facing 프리픽스 리스트를 배포 리전에서
// 자동 조회한다(fromLookup) — synth/deploy 시 크리덴셜 필요, 결과는
// cdk.context.json에 캐시된다(커밋 대상).
new AipdsHostingStack(app, 'AipdsHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  hostedUiDomain: auth.hostedUiDomain,
});

// 프로토타입 프리뷰 전용 오리진(lib/aipds-preview-stack.ts). HostingStack이 만든 것을
// **참조만** 하므로 그 스택을 다시 배포하지 않는다(EC2 교체를 피한다). 세 값은 HostingStack의
// 출력과 리소스에서 읽어 넘긴다 — 없으면 이 스택을 만들지 않으므로 `--all`은 예전과 같다.
const previewOriginDns = process.env.AIPDS_PREVIEW_ORIGIN_DNS;
const originVerifySecretArn = process.env.AIPDS_ORIGIN_VERIFY_SECRET_ARN;
const instanceRoleArn = process.env.AIPDS_INSTANCE_ROLE_ARN;
if (previewOriginDns && originVerifySecretArn && instanceRoleArn) {
  new AipdsPreviewStack(app, 'AipdsPreviewStack', {
    env,
    originDnsName: previewOriginDns,
    originVerifySecretArn,
    instanceRoleArn,
  });
}
