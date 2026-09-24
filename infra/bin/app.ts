#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { addSandboxStacks, pinnedFromEnv } from '../lib/sandbox-stacks';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

// 브라우저가 아티팩트 버킷으로 직접 PUT할 출처(프로젝트 가져오기의 presigned 업로드) 중
// **추가로 허용할 것**(예: 커스텀 도메인). 앱 자신의 오리진(호스팅 스택의 CloudFront)은 여기서
// 넘기지 않는다 — 그 스택이 이 버킷에 의존하므로 순환이고, 인스턴스가 스스로 버킷 CORS에
// 더한다(lib/aipds-upload-cors-stack.ts, infra/scripts/aipds-harden sync).
//
//   AIPDS_UPLOAD_ORIGINS=https://pds.example.com npx cdk deploy --all
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
const hosting = new AipdsHostingStack(app, 'AipdsHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  hostedUiDomain: auth.hostedUiDomain,
});

// 프리뷰 오리진과 샌드박스용 Bedrock 전용 롤. 새 환경은 HostingStack에서 참조로, 떠 있는 환경은
// env로 고정한 값으로 만든다(lib/sandbox-stacks.ts — 고정하지 않으면 HostingStack까지 배포된다).
//
//   AIPDS_INSTANCE_ROLE_ARN=<InstanceRole ARN> AIPDS_PREVIEW_ORIGIN_DNS=<EIP DNS> \
//   AIPDS_ORIGIN_VERIFY_SECRET_ARN=<OriginVerifyHeader ARN> AIPDS_ARTIFACTS_BUCKET=<bucket> \
//     npx cdk deploy AipdsAgentCredsStack AipdsPreviewStack AipdsUploadCorsStack
addSandboxStacks(app, env, drill, hosting, pinnedFromEnv(process.env));
