import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';

// 프로젝트 가져오기는 브라우저가 앱 오리진에서 아티팩트 버킷으로 직접 PUT한다
// (backend/aipds/import_staging.py). 그러려면 버킷 CORS가 앱 오리진을 알아야 하는데, 그 오리진은
// HostingStack의 CloudFront이고 HostingStack은 버킷(DrillStack)에 의존한다 — 템플릿으로는 순환이다.
//
// 그래서 **인스턴스가 스스로 맞춘다**(infra/scripts/aipds-harden sync, 2분마다). 인스턴스는 자기 앱
// 오리진(APP_BASE_URL)과 버킷(AIPDS_S3_BUCKET)을 안다. 버킷 CORS의 PUT 규칙에 그 오리진이 없으면
// 더한다 — DrillStack이 CORS를 다시 쓰더라도(AIPDS_UPLOAD_ORIGINS 없이 재배포) 몇 분 안에 돌아온다.
//
// 이 스택은 그 일에 필요한 권한만 인스턴스 롤에 붙인다: 그 버킷의 CORS 읽기·쓰기. HostingStack을
// 고치지 않는다(lib/aipds-preview-stack.ts와 같은 사정).

export interface UploadCorsStackProps extends cdk.StackProps {
  /** HostingStack의 인스턴스 롤. */
  instanceRoleArn: string;
  /** DrillStack의 아티팩트 버킷. */
  bucketArn: string;
}

export class AipdsUploadCorsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: UploadCorsStackProps) {
    super(scope, id, props);
    // id는 다른 스택의 것('InstanceRole', 'PreviewInstanceRole')과 달라야 한다 — 가져온 롤에 붙는
    // 인라인 정책의 이름이 이 경로에서 나온다(test/instance-params.assert.ts).
    const instanceRole = iam.Role.fromRoleArn(this, 'CorsInstanceRole', props.instanceRoleArn, {
      mutable: true,
    });
    instanceRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['s3:GetBucketCORS', 's3:PutBucketCORS'],
      resources: [props.bucketArn],
    }));
  }
}
