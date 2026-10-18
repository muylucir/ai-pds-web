import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';

// Project import has the browser PUT directly from the app origin to the artifact bucket
// (backend/aipds/import_staging.py). That requires the bucket CORS to know the app origin, but that origin is
// HostingStack's CloudFront, and HostingStack depends on the bucket (DrillStack) — in templates that is circular.
//
// So **the instance aligns it by itself** (infra/scripts/aipds-harden sync, every 2 minutes). The instance knows its own app
// origin (APP_BASE_URL) and bucket (AIPDS_S3_BUCKET). If the bucket CORS PUT rule lacks that origin, it
// adds it — even if DrillStack rewrites the CORS (redeployed without AIPDS_UPLOAD_ORIGINS), it comes back within minutes.
//
// This stack attaches only the permissions needed for that to the instance role: CORS read/write on that bucket. It does not
// modify HostingStack (same situation as lib/aipds-preview-stack.ts).

export interface UploadCorsStackProps extends cdk.StackProps {
  /** HostingStack's instance role. */
  instanceRoleArn: string;
  /** DrillStack's artifact bucket. */
  bucketArn: string;
}

export class AipdsUploadCorsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: UploadCorsStackProps) {
    super(scope, id, props);
    // The id must differ from the other stacks' ('InstanceRole', 'PreviewInstanceRole') — the name of the inline policy
    // attached to the imported role comes from this path (test/instance-params.assert.ts).
    const instanceRole = iam.Role.fromRoleArn(this, 'CorsInstanceRole', props.instanceRoleArn, {
      mutable: true,
    });
    instanceRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['s3:GetBucketCORS', 's3:PutBucketCORS'],
      resources: [props.bucketArn],
    }));
  }
}
