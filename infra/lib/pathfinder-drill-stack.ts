import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';

const MODEL = 'global.anthropic.claude-opus-4-8';
const MODEL_FAMILY = 'anthropic.claude-opus-4-8';

export class PathfinderDrillStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // Artifacts bucket — 프로젝트 산출물(projects/*)과 strands 세션(sessions/*).
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // 백엔드 프로세스가 assume하는 실행 롤: Bedrock invoke + S3(projects/* & sessions/*).
    // 백엔드가 EC2/컨테이너 인스턴스 프로파일로 이 롤을 맡거나, 롤 정책을 그대로
    // 인스턴스 프로파일에 부여한다(호스트 자격증명 모델, spec §2).
    const backendRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.AccountPrincipal(account),
      description: 'Pathfinder backend: Bedrock invoke + artifacts/session S3 access.',
    });
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/${MODEL_FAMILY}*`,
      ],
    }));
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/projects/*`, `${bucket.bucketArn}/sessions/*`],
    }));
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['projects/*', 'sessions/*'] } },
    }));

    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BackendRoleArn', { value: backendRole.roleArn });
    // 스택이 실제로 배포되는 리전(bin/app.ts의 env.region으로 결정).
    new cdk.CfnOutput(this, 'Region', { value: this.region });
  }
}
