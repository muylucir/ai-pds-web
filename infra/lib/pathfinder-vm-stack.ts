import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import { MODEL, MODEL_FAMILY } from './backend-permissions';

// MicroVM 리소스는 Tokyo 고정 — ap-northeast-2(서울, 드릴/호스팅 스택)에는 Lambda
// MicroVMs 서비스가 없다(2026-07-17 확인: list-microvm-images가 서울에서
// AccessDeniedException). 크로스 리전 배포이므로 별도 스택으로 분리한다.
const REGION = 'ap-northeast-1';
const BASE_IMAGE_ARN = `arn:aws:lambda:${REGION}:aws:microvm-image:al2023-1`;

export class PathfinderVmStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // 아티팩트 버킷 없음 — 파일은 백엔드가 중개한다(spec §5). S3는 서울 드릴
    // 스택(PathfinderDrillStack)이 소유.

    // Harness code asset (infra/build/harness, package-harness.sh 산출물:
    // Dockerfile이 루트에 위치). aws_s3_assets는 이를 zip으로 S3에 업로드한다.
    const harnessAsset = new s3assets.Asset(this, 'HarnessCode', {
      path: path.join(__dirname, '..', 'build', 'harness'),
    });

    // Build role: 코드 아티팩트 읽기 + 빌드 로그 쓰기. Confused-deputy 가드:
    // 이 계정의 MicroVM 이미지-빌드 서비스가 microvm-image 리소스를 대신해
    // 동작할 때만 assume 가능. Trust principal은 순수 Lambda 서비스
    // (`lambda.amazonaws.com`) — `microvms.lambda...`가 아니다. IAM은 후자를
    // 유효하지 않은 principal로 거부한다(배포 시 확인됨; Lambda MicroVMs IAM
    // 레퍼런스는 두 롤 모두 `lambda.amazonaws.com`을 사용).
    const microvmImageArn = `arn:aws:lambda:${REGION}:${account}:microvm-image:*`;
    const buildRole = new iam.Role(this, 'BuildRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': account },
          ArnLike: { 'aws:SourceArn': microvmImageArn },
        },
      }),
    });
    harnessAsset.grantRead(buildRole);
    buildRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${REGION}:${account}:log-group:/pathfinder/microvm/*`],
    }));

    // Execution role: 실행 중인 VM이 assume. Bedrock invoke만 — S3 statement는
    // 전부 삭제(스펙 §5: 파일은 백엔드가 중개하므로 VM은 S3에 직접 접근하지
    // 않는다). pilot1-validated 셰이프: inference-profile ARN + foundation-model
    // 와일드카드(backend-permissions.ts의 MODEL/MODEL_FAMILY 재사용).
    const execRole = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': account },
          ArnLike: { 'aws:SourceArn': microvmImageArn },
        },
      }),
    });
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/${MODEL_FAMILY}*`,
      ],
    }));

    // 로그 그룹·이미지 이름은 'prototype' 네임스페이스를 쓴다: Tokyo에 남아
    // 있는 이전(7/18) PathfinderDrillStack이 `/pathfinder/microvm/harness`
    // 로그 그룹과 `pathfinder-harness` 이미지를 아직 소유하고 있어서, 같은
    // 이름을 쓰면 "already exists"로 change set 검증에서 배포가 막힌다.
    // 그 스택은 현재 서비스 중인 데이터 버킷도 소유하므로 삭제하지 않고
    // 이름만 분리한다(리소스 충돌 없이 병존).
    const logGroup = new logs.LogGroup(this, 'MicrovmLogs', {
      logGroupName: '/pathfinder/microvm/prototype',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      retention: logs.RetentionDays.ONE_WEEK,
    });

    // The MicroVM image (L1 CfnMicrovmImage; L2 does not exist yet). Hooks are
    // served by harness/hooks.py on port 9000 at the fixed convention paths
    // /ready (gates the build snapshot) and /validate (gates resume-from-
    // snapshot); the ready/validate PROPERTIES are ENABLED/DISABLED toggles
    // (the platform calls the fixed paths when enabled), NOT the path strings —
    // confirmed at deploy-time CFN validation. Env is baked = BootSpec.env().
    const image = new lambda.CfnMicrovmImage(this, 'HarnessImage', {
      name: 'pathfinder-prototype-harness',
      description: 'Pathfinder prototype harness: Claude Code driver, Bedrock-backed.',
      baseImageArn: BASE_IMAGE_ARN,
      baseImageVersion: '1', // major version of the al2023-1 managed base; the service rejects 'latest' (expects a single major number, confirmed at deploy time)
      buildRoleArn: buildRole.roleArn,
      codeArtifact: { uri: harnessAsset.s3ObjectUrl },
      environmentVariables: [
        { key: 'CLAUDE_CODE_USE_BEDROCK', value: '1' },
        // AWS_REGION is a RESERVED key — the MicroVM runtime injects it
        // automatically (rejected as a baked env var at deploy time). BootSpec.env()
        // still sets it for the LOCAL/non-MicroVM path; here the VM inherits it.
        { key: 'ANTHROPIC_MODEL', value: MODEL },
      ],
      additionalOsCapabilities: [],
      egressNetworkConnectors: [],
      cpuConfigurations: [{ architecture: 'ARM_64' }],
      resources: [{ minimumMemoryInMiB: 2048 }],
      hooks: {
        port: 9000,
        microvmImageHooks: {
          ready: 'ENABLED',
          readyTimeoutInSeconds: 300,
          validate: 'ENABLED',
          validateTimeoutInSeconds: 60,
        },
      },
      logging: { cloudWatch: { logGroup: logGroup.logGroupName } },
    });
    // Runtime hooks (run/resume/suspend/terminate) are fast-notification and
    // OPTIONAL; skipped for now (YAGNI) — our lifecycle is driven by the
    // controller polling get-microvm, not by in-VM runtime-hook callbacks.

    new cdk.CfnOutput(this, 'ImageArn', { value: image.attrImageArn });
    new cdk.CfnOutput(this, 'ExecutionRoleArn', { value: execRole.roleArn });
    new cdk.CfnOutput(this, 'Region', { value: REGION });
  }
}
