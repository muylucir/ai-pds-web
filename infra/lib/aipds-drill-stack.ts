import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { backendPolicyStatements } from './backend-permissions';

export class AipdsDrillStack extends cdk.Stack {
  public readonly artifactsBucket: s3.Bucket;

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
    this.artifactsBucket = bucket;

    // 백엔드 프로세스가 assume하는 실행 롤: Bedrock invoke + S3(projects/* & sessions/*).
    // 백엔드가 EC2/컨테이너 인스턴스 프로파일로 이 롤을 맡거나, 롤 정책을 그대로
    // 인스턴스 프로파일에 부여한다(호스트 자격증명 모델, spec §2).
    const backendRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.AccountPrincipal(account),
      description: 'AI-PDS backend: Bedrock invoke + artifacts/session S3 access.',
    });
    for (const stmt of backendPolicyStatements(bucket, account)) {
      backendRole.addToPolicy(stmt);
    }

    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BackendRoleArn', { value: backendRole.roleArn });
    // 스택이 실제로 배포되는 리전(bin/app.ts의 env.region으로 결정).
    new cdk.CfnOutput(this, 'Region', { value: this.region });
  }
}
