import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';

// 프로토타입 프리뷰 전용 오리진.
//
// **왜 따로 두는가.** 프로토타입은 빌드 에이전트가 쓴 코드이고 신뢰 대상이 아니다. 앱과
// 같은 오리진에서 서빙하면 앱 세션 쿠키가 프로토타입 요청에 실리고, Next의 /api 프록시가
// 그것을 Cognito Bearer 토큰으로 바꿔 프로토타입 서버 프로세스까지 가져간다. 프로토타입의
// JS는 같은 오리진이라 보는 사람의 권한으로 앱 API를 부를 수도 있다. 이 배포가 같은 EC2의
// `/api/proto/*`만 서빙하고, 앱 오리진은 프로토타입을 서빙하지 않는다
// (backend/aipds/preview_surface.py). `*.cloudfront.net`은 공개 접미사 목록에 있어 두 배포는
// 서로 다른 사이트다 — 앱 쿠키가 여기 실릴 길이 없다.
//
// **왜 HostingStack 안이 아니라 별도 스택인가.** HostingStack을 배포하면 EC2가 교체될 수
// 있다: AMI가 고정되지 않아(`latestAmazonLinux2023()`) 새 AMI가 나와 있으면 어떤 변경이든
// 교체되고, nginx 설정이 user-data 안이다(`userDataCausesReplacement`). 이 스택은 EC2를
// 참조만 하므로(오리진 DNS, 비밀 헤더 시크릿, 인스턴스 롤) 배포해도 인스턴스는 그대로다.
// 인스턴스 쪽 변경도 없다 — nginx는 `server_name _`로 이 Host도 받고, 두 비밀 헤더를
// Next 프록시가 백엔드까지 넘긴다.
//
// **요청이 어느 표면으로 왔는지.** 이 배포만 `X-Preview-Verify`를 붙인다. 기존 배포가 붙이는
// `X-Origin-Verify`도 함께 붙인다 — nginx가 그것으로 CloudFront 밖의 직접 접근을 막는다.

export interface PreviewStackProps extends cdk.StackProps {
  /** EC2의 퍼블릭 DNS(EIP). CloudFront 오리진은 IP를 받지 않는다. */
  originDnsName: string;
  /** HostingStack의 `X-Origin-Verify` 시크릿(전체 ARN). nginx가 이 값을 검사한다. */
  originVerifySecretArn: string;
  /** HostingStack의 인스턴스 롤. 백엔드가 프리뷰 비밀 헤더 값을 읽는다. */
  instanceRoleArn: string;
}

/** 프로토타입 경로. 백엔드가 보는 `/proto/*`에 Next의 `/api` 마운트가 붙은 브라우저 경로다. */
export const PREVIEW_PATH_PATTERN = '/api/proto/*';

export class AipdsPreviewStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PreviewStackProps) {
    super(scope, id, props);

    const previewSecret = new secretsmanager.Secret(this, 'PreviewVerifyHeader', {
      description: 'X-Preview-Verify shared secret (preview CloudFront custom header <-> backend).',
      generateSecretString: { passwordLength: 32, excludePunctuation: true },
    });
    // 인스턴스 롤은 HostingStack의 것이다 — 그 스택을 고치지 않고 권한을 주려면 시크릿 쪽의
    // 리소스 정책이어야 한다.
    previewSecret.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      principals: [new iam.ArnPrincipal(props.instanceRoleArn)],
      resources: ['*'],
    }));

    const originVerify = secretsmanager.Secret.fromSecretCompleteArn(
      this, 'OriginVerifyHeader', props.originVerifySecretArn);

    const origin = new origins.HttpOrigin(props.originDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      readTimeout: cdk.Duration.seconds(60),
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        // CFN 동적 참조로 배포 시 해석된다 — 템플릿에 평문이 남지 않는다.
        'X-Origin-Verify': originVerify.secretValue.unsafeUnwrap(),
        'X-Preview-Verify': previewSecret.secretValue.unsafeUnwrap(),
      },
    });

    // 프로토타입 경로 밖은 전부 404다 — 이 도메인에서 앱 화면이 열리면 안 된다(로그인은
    // 콜백 URL이 막지만, 앱 페이지가 프로토타입과 같은 오리진에 있을 이유가 없다).
    const notFound = new cloudfront.Function(this, 'NotFound', {
      comment: 'AI-PDS preview: anything outside /api/proto/* is 404.',
      code: cloudfront.FunctionCode.fromInline(
        'function handler(event) { return { statusCode: 404, statusDescription: "Not Found" }; }'),
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'AI-PDS prototype previews (separate origin from the app).',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        functionAssociations: [{
          function: notFound,
          eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
        }],
      },
      additionalBehaviors: {
        [PREVIEW_PATH_PATTERN]: {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
        },
      },
    });

    new cdk.CfnOutput(this, 'PreviewOrigin', {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'PreviewSecretArn', { value: previewSecret.secretArn });
  }
}
