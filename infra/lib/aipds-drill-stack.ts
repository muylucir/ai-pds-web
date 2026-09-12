import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { backendPolicyStatements } from './backend-permissions';

export interface AipdsDrillStackProps extends cdk.StackProps {
  /** 브라우저가 이 버킷으로 직접 PUT할 때 허용되는 출처.
   *
   *  프로젝트 가져오기는 번들을 presigned PUT으로 S3에 **직접** 올린다 —
   *  nginx의 client_max_body_size(인스턴스 user-data 안의 값이므로 고치는 것이 곧
   *  EC2 교체다)를 무관하게 만드는 유일한 길이다. 그 요청은 크로스 오리진이므로
   *  버킷에 CORS 규칙이 있어야 하고, 없으면 preflight에서 막힌다.
   *
   *  **값을 prop으로 받는 이유.** 실제 출처는 호스팅 스택의 CloudFront 도메인인데
   *  그 스택이 이미 이 버킷에 의존하므로 상호 참조는 순환이다. 그래서 bin/app.ts가
   *  환경변수/컨텍스트에서 읽어 넣는다: 첫 배포에는 도메인이 아직 없으니 값 없이
   *  배포하고, 도메인이 생긴 뒤 이 스택만 다시 배포한다(버킷 전용 스택이라 EC2를
   *  교체하지 않는다).
   *
   *  와일드카드를 쓰지 않는다. 버킷은 BLOCK_ALL이고 presigned URL이 유일한
   *  접근로지만, CORS를 넓히면 다른 사이트의 스크립트가 사용자의 유효한 presigned
   *  URL을 재사용할 표면이 생긴다.
   */
  readonly uploadOrigins?: string[];
}

/** 로컬 개발의 프론트엔드. 배포 도메인이 지정되지 않아도 이 출처는 항상 허용한다 —
 *  개발자가 자기 박스에서 가져오기를 시험할 수 있어야 한다. */
export const LOCAL_UPLOAD_ORIGIN = 'http://localhost:3000';

/** 스테이징된 번들이 방치됐을 때 사라지는 기한(일).
 *
 *  임포트는 성공·실패 어느 쪽에서도 자기 스테이징 객체를 지운다. 이 규칙이 걷는
 *  것은 그 사이에 사용자가 창을 닫은 경우다 — 업로드는 끝났지만 임포트 요청이 오지
 *  않은 번들. 그것이 남으면 프로젝트 하나 크기의 객체가 조용히 쌓인다. */
export const IMPORT_STAGING_EXPIRY_DAYS = 1;

export class AipdsDrillStack extends cdk.Stack {
  public readonly artifactsBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: AipdsDrillStackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    const uploadOrigins = [
      LOCAL_UPLOAD_ORIGIN,
      ...(props?.uploadOrigins ?? []),
    ];

    // Artifacts bucket — 프로젝트 산출물(projects/*), strands 세션(sessions/*),
    // 그리고 가져오기 스테이징(imports/*).
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      // PUT 하나뿐이다. 브라우저는 업로드만 직접 하고, 내보내기는 백엔드가
      // 스트리밍하므로(응답 본문에는 nginx 상한이 없다) GET을 열 이유가 없다.
      // `content-type`은 서명에 포함되는 헤더이므로 허용목록에 있어야 한다
      // (backend/aipds/import_staging.py의 CONTENT_TYPE).
      cors: [{
        allowedOrigins: uploadOrigins,
        allowedMethods: [s3.HttpMethods.PUT],
        allowedHeaders: ['content-type'],
        exposedHeaders: ['ETag'],
        maxAge: 3000,
      }],
      lifecycleRules: [{
        id: 'expire-abandoned-import-staging',
        prefix: 'imports/',
        expiration: cdk.Duration.days(IMPORT_STAGING_EXPIRY_DAYS),
        // 멀티파트 업로드가 중간에 끊긴 조각도 함께 걷는다. boto3의 관리형
        // 전송과 브라우저의 단일 PUT 모두 조각을 남기지 않지만, 남는 경로가
        // 있다면 그것도 방치된 스테이징이다.
        abortIncompleteMultipartUploadAfter:
          cdk.Duration.days(IMPORT_STAGING_EXPIRY_DAYS),
      }],
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
