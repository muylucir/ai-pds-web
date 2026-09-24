import * as cdk from 'aws-cdk-lib';
import { AipdsHostingStack } from './aipds-hosting-stack';
import { AipdsPreviewStack } from './aipds-preview-stack';
import { AipdsAgentCredsStack } from './aipds-agent-creds-stack';
import { AipdsDrillStack } from './aipds-drill-stack';
import { AipdsUploadCorsStack } from './aipds-upload-cors-stack';

// 인스턴스가 부팅 뒤에 스스로 켜는 것들의 스택: 프로토타입 프리뷰 전용 오리진
// (aipds-preview-stack.ts), 샌드박스 프로세스용 Bedrock 전용 롤(aipds-agent-creds-stack.ts), 버킷
// CORS를 맞출 권한(aipds-upload-cors-stack.ts). 셋 다 HostingStack의 것(인스턴스 롤, 헤더 시크릿,
// EIP DNS)을 참조만 하고, 인스턴스가 infra/scripts/aipds-harden sync로 반영한다.
//
// 값을 어디서 받는가가 두 가지다.
//   - **새 환경(기본):** HostingStack에서 스택 간 참조로 받는다. `cdk deploy --all` 한 번이면 된다 —
//     인스턴스는 HostingStack과 함께 먼저 부팅하지만, 두 스택이 SSM에 값을 쓰면 몇 분 안에 스스로
//     래퍼·IMDS 차단·프리뷰를 켠다.
//   - **이미 떠 있는 환경(pinned):** 값을 env로 고정한다. 스택 간 참조는 두 스택을 HostingStack에
//     묶어 `cdk deploy AipdsAgentCredsStack`이 HostingStack까지 배포하게 만든다 — 그 배포는 EC2를
//     교체할 수 있다(AMI 미고정, userDataCausesReplacement). 고정하면 두 스택만 배포된다.
//     (운영 중인 HostingStack에는 교체·삭제를 거부하는 스택 정책도 건다 — README.)

export interface PinnedHosting {
  instanceRoleArn: string;
  originDnsName: string;
  originVerifySecretArn: string;
  /** 아티팩트 버킷 이름. 참조로 받으면 DrillStack에 묶인다 — 같은 이유로 고정한다. */
  artifactsBucket: string;
}

const PINNED_ENV = {
  instanceRoleArn: 'AIPDS_INSTANCE_ROLE_ARN',
  originDnsName: 'AIPDS_PREVIEW_ORIGIN_DNS',
  originVerifySecretArn: 'AIPDS_ORIGIN_VERIFY_SECRET_ARN',
  artifactsBucket: 'AIPDS_ARTIFACTS_BUCKET',
} as const;

/** env의 고정 값. 하나라도 있으면 전부 있어야 한다 — 섞으면 일부 스택만 다른 스택에 묶인다. */
export function pinnedFromEnv(e: NodeJS.ProcessEnv): PinnedHosting | undefined {
  const values = Object.fromEntries(
    Object.entries(PINNED_ENV).map(([key, name]) => [key, e[name]])) as Record<string, string | undefined>;
  const set = Object.values(values).filter((v) => v);
  if (set.length === 0) return undefined;
  if (set.length !== Object.keys(PINNED_ENV).length) {
    throw new Error(`${Object.values(PINNED_ENV).join(', ')} pin a running environment together `
      + '— set all or none');
  }
  return values as unknown as PinnedHosting;
}

export function addSandboxStacks(app: cdk.App, env: cdk.Environment, drill: AipdsDrillStack,
                                 hosting: AipdsHostingStack, pinned?: PinnedHosting) {
  const instanceRoleArn = pinned?.instanceRoleArn ?? hosting.instanceRole.roleArn;
  const preview = new AipdsPreviewStack(app, 'AipdsPreviewStack', {
    env,
    originDnsName: pinned?.originDnsName ?? hosting.originDnsName,
    originVerifySecretArn: pinned?.originVerifySecretArn ?? hosting.originVerifySecret.secretArn,
    instanceRoleArn,
  });
  const agentCreds = new AipdsAgentCredsStack(app, 'AipdsAgentCredsStack', { env, instanceRoleArn });
  const uploadCors = new AipdsUploadCorsStack(app, 'AipdsUploadCorsStack', {
    env,
    instanceRoleArn,
    bucketArn: pinned ? `arn:aws:s3:::${pinned.artifactsBucket}` : drill.artifactsBucket.bucketArn,
  });
  return { preview, agentCreds, uploadCors };
}
