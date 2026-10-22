import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { INSTANCE_PARAMS } from './instance-params';

// An origin dedicated to prototype previews.
//
// **Why it is separate.** Prototypes are code written by the build agent and are not trusted. Serving
// them from the same origin as the app would put the app session cookie on prototype requests, and Next's
// /api proxy would turn it into a Cognito Bearer token and carry it all the way to the prototype server
// process. Being same-origin, the prototype's JS could also call the app API with the viewer's
// permissions. This distribution serves only `/api/proto/*` of the same EC2, and the app origin does not
// serve prototypes (backend/aipds/preview_surface.py). `*.cloudfront.net` is on the public suffix list, so
// the two distributions are different sites — there is no way for app cookies to ride along here.
//
// **Why a separate stack rather than inside HostingStack.** Deploying HostingStack can replace the EC2:
// the AMI is not pinned (`latestAmazonLinux2023()`), so if a new AMI is out any change triggers a
// replacement, and the nginx config is inside user-data (`userDataCausesReplacement`). This stack only
// references the EC2 (its VPC origin, the secret header secret, the instance role), so deploying it leaves
// the instance as is. There are no instance-side changes either — nginx accepts this Host too via
// `server_name _`, and the Next proxy forwards both secret headers to the backend.
//
// **Which surface a request came through.** Only this distribution adds `X-Preview-Verify`. It also adds the
// `X-Origin-Verify` the existing distribution adds — nginx uses it to block direct access from outside CloudFront.
//
// **How the instance learns these values.** Both outputs are also written as SSM parameters (lib/instance-params.ts). A new
// instance reads them at boot and tells the backend the preview origin (aipds-harden `boot`).

export interface PreviewStackProps extends cdk.StackProps {
  /** HostingStack's VPC origin. This distribution reaches the instance through the same one. */
  vpcOriginId: string;
  /** The instance's private DNS name — the origin domain for a VPC origin to EC2. */
  originPrivateDns: string;
  /** HostingStack's `X-Origin-Verify` secret (full ARN). nginx checks this value. */
  originVerifySecretArn: string;
  /** HostingStack's instance role. The backend reads the preview secret header value. */
  instanceRoleArn: string;
}

/** Prototype path. The browser path: the backend's `/proto/*` with Next's `/api` mount prefixed. */
export const PREVIEW_PATH_PATTERN = '/api/proto/*';

export class AipdsPreviewStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PreviewStackProps) {
    super(scope, id, props);

    const previewSecret = new secretsmanager.Secret(this, 'PreviewVerifyHeader', {
      description: 'X-Preview-Verify shared secret (preview CloudFront custom header <-> backend).',
      generateSecretString: { passwordLength: 32, excludePunctuation: true },
    });
    // The instance role belongs to HostingStack — to grant permissions without modifying that stack, it has
    // to be a resource policy on the secret side.
    previewSecret.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      principals: [new iam.ArnPrincipal(props.instanceRoleArn)],
      resources: ['*'],
    }));

    const originVerify = secretsmanager.Secret.fromSecretCompleteArn(
      this, 'OriginVerifyHeader', props.originVerifySecretArn);

    // The origin leg is the app distribution's VPC origin — private connectivity, HTTP on port 80 for the
    // reason in the VPC origin comment of aipds-hosting-stack.ts (README "CloudFront → instance is private").
    // The prototype access cookie and both secret headers travel on this leg.
    const vpcOrigin = cloudfront.VpcOrigin.fromVpcOriginId(this, 'AppVpcOrigin', props.vpcOriginId);
    const origin = origins.VpcOrigin.withVpcOrigin(vpcOrigin, {
      domainName: props.originPrivateDns,
      readTimeout: cdk.Duration.seconds(60),
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        // Resolved at deploy time via a CFN dynamic reference — no plaintext remains in the template.
        'X-Origin-Verify': originVerify.secretValue.unsafeUnwrap(),
        'X-Preview-Verify': previewSecret.secretValue.unsafeUnwrap(),
      },
    });

    // Everything outside the prototype path is 404 — app screens must not open on this domain (login is
    // blocked by the callback URLs, but there is no reason for app pages to share an origin with prototypes).
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

    const previewOrigin = `https://${distribution.distributionDomainName}`;
    // The id must differ from AgentCredsStack's ('InstanceRole') — the name of the inline policy attached to the
    // imported role comes from this path, so if they matched, both stacks would collide trying to create a same-named policy on the same role.
    const instanceRole = iam.Role.fromRoleArn(this, 'PreviewInstanceRole', props.instanceRoleArn, {
      mutable: true,
    });
    for (const [id, name, value] of [
      ['PreviewOriginParam', INSTANCE_PARAMS.previewOrigin, previewOrigin],
      ['PreviewSecretArnParam', INSTANCE_PARAMS.previewSecretArn, previewSecret.secretArn],
    ] as const) {
      new ssm.StringParameter(this, id, {
        parameterName: name,
        stringValue: value,
        description: 'AI-PDS preview; read at boot by infra/scripts/aipds-harden.',
      }).grantRead(instanceRole);
    }

    new cdk.CfnOutput(this, 'PreviewOrigin', { value: previewOrigin });
    new cdk.CfnOutput(this, 'PreviewSecretArn', { value: previewSecret.secretArn });
  }
}
