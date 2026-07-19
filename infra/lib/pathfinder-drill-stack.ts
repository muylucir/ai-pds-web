import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';

const REGION = 'ap-northeast-1';
const MODEL = 'global.anthropic.claude-sonnet-5';
const BASE_IMAGE_ARN = `arn:aws:lambda:${REGION}:aws:microvm-image:al2023-1`;

export class PathfinderDrillStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // Artifacts bucket — drill scope: destroyed with the stack.
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // Harness code asset (infra/build/harness produced by package-harness.sh:
    // Dockerfile at root). aws_s3_assets zips it into an S3 object.
    const harnessAsset = new s3assets.Asset(this, 'HarnessCode', {
      path: path.join(__dirname, '..', 'build', 'harness'),
    });

    // Build role: read the code artifact + write build logs. Confused-deputy
    // guard: only the MicroVM image-build service in THIS account, acting for a
    // microvm-image resource, may assume it. Trust principal is the plain
    // Lambda service (`lambda.amazonaws.com`) — NOT `microvms.lambda...`, which
    // IAM rejects as an invalid principal (confirmed at deploy time; the
    // Lambda MicroVMs IAM reference uses `lambda.amazonaws.com` for both roles).
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

    // Execution role: assumed by the RUNNING VM. Bedrock invoke + S3 access
    // SCOPED TO THE SESSION-STATE PREFIX ONLY (S3SessionManager persistence,
    // spec §2). The artifacts prefix (projects/*) stays unreachable from the
    // VM — the durable-workspace boundary is preserved.
    // pilot1-validated shape: inference-profile ARN + foundation-model wildcard.
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
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5*`,
      ],
    }));
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/sessions/*`],
    }));
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': 'sessions/*' } },
    }));

    const logGroup = new logs.LogGroup(this, 'MicrovmLogs', {
      logGroupName: '/pathfinder/microvm/harness',
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
      name: 'pathfinder-harness',
      description: 'Pathfinder drill harness: Claude Code driver + aiplc-rules, Bedrock-backed.',
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
        { key: 'PATHFINDER_DRIVER', value: 'strands' },
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
    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'Region', { value: REGION });
  }
}
