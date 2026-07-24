import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';

export const MODEL = 'global.anthropic.claude-opus-4-8';
export const MODEL_FAMILY = 'anthropic.claude-opus-4-8';

// 백엔드(드릴 롤 또는 EC2 인스턴스 롤)가 필요로 하는 공통 권한:
// Bedrock invoke + 아티팩트 버킷 projects/*·sessions/* 읽기/쓰기/목록.
export function backendPolicyStatements(
  bucket: s3.IBucket,
  account: string,
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/${MODEL_FAMILY}*`,
      ],
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

// 백엔드(드릴 롤 또는 EC2 인스턴스 롤)가 Tokyo MicroVM을 제어하는 데 필요한 권한.
// 액션 프리픽스는 `lambda-microvms:`가 아니라 `lambda:`다 — boto3/CLI 서비스명은
// `lambda-microvms`지만 IAM 액션 네임스페이스는 Lambda 서비스에 속한다(공식 문서
// https://docs.aws.amazon.com/lambda/latest/dg/microvms-security.html의 "IAM
// permissions reference" 표 + "Resource ARN formats" 확인, 2026-07-24). RunMicrovm은
// 이미지를 읽어야 하므로 microvm:*과 microvm-image:* 둘 다 리소스로 포함한다.
// backendPolicyStatements와는 별도로 두 스택(드릴 백엔드 롤, 호스팅 인스턴스 롤)에서
// 명시적으로 합쳐 쓴다 — 이 헬퍼는 S3/Bedrock 시맨틱과 무관하다.
export function microvmControlStatements(
  account: string,
  vmRegion = 'ap-northeast-1',
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: [
        'lambda:RunMicrovm',
        'lambda:GetMicrovm',
        'lambda:TerminateMicrovm',
        'lambda:ListMicrovms',
        'lambda:CreateMicrovmAuthToken',
      ],
      resources: [
        `arn:aws:lambda:${vmRegion}:${account}:microvm:*`,
        `arn:aws:lambda:${vmRegion}:${account}:microvm-image:*`,
      ],
    }),
  ];
}
