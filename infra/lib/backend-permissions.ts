import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';

// 런타임 기본 모델 — user-data가 ANTHROPIC_MODEL로 내보낸다. 권한과 별개로
// 유지한다: 이 값을 바꾸는 것은 배포 동작을 바꾸는 것이고, 아래 목록을 늘리는
// 것은 "바꿀 수 있게 두는 것"이다.
export const MODEL = 'global.anthropic.claude-opus-4-8';

// invoke를 허용하는 모델. MODEL 하나만 허용하면 ANTHROPIC_MODEL을 바꿔보는
// 순간 AccessDenied가 나고, 그 실패는 첫 대화 턴에 가서야 드러난다(README의
// 트러블슈팅 항목이 그 증상이다). env 한 줄로 전환할 수 있게 미리 넓혀 둔다.
// inference-profile은 global.* 프리픽스, foundation-model은 없는 형태 —
// 둘 다 필요하다(프로파일 경유 호출이 내부적으로 후자를 참조한다).
const INVOKABLE_MODELS = [
  'claude-opus-5',
  'claude-sonnet-5',
  'claude-opus-4-8',
] as const;

// 백엔드(드릴 롤 또는 EC2 인스턴스 롤)가 필요로 하는 공통 권한:
// Bedrock invoke + 아티팩트 버킷 projects/*·sessions/* 읽기/쓰기/목록.
export function backendPolicyStatements(
  bucket: s3.IBucket,
  account: string,
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODELS.flatMap((m) => [
        `arn:aws:bedrock:*:${account}:inference-profile/global.anthropic.${m}`,
        `arn:aws:bedrock:*::foundation-model/anthropic.${m}*`,
      ]),
    }),
    new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/projects/*`, `${bucket.bucketArn}/sessions/*`],
    }),
    new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['projects/*', 'sessions/*'] } },
    }),
  ];
}
