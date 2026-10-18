import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { backendPolicyStatements } from './backend-permissions';

export interface AipdsDrillStackProps extends cdk.StackProps {
  /** Origins allowed when the browser PUTs directly to this bucket.
   *
   *  Project import uploads the bundle to S3 **directly** with a presigned PUT —
   *  the only way to make nginx's client_max_body_size and the Next middleware's body
   *  limit (both tuned for attachment uploads) irrelevant. That request is cross-origin,
   *  so the bucket needs a CORS rule; without one, it is blocked at preflight.
   *
   *  **Why the value is taken as a prop.** The real origin is the hosting stack's
   *  CloudFront domain, but that stack already depends on this bucket, so a mutual
   *  reference would be circular. So bin/app.ts reads it from env vars/context: on the
   *  first deploy the domain doesn't exist yet, so deploy without a value, and once the
   *  domain exists, redeploy only this stack (it's a bucket-only stack, so it doesn't
   *  replace EC2).
   *
   *  No wildcard. The bucket is BLOCK_ALL and presigned URLs are the only access
   *  path, but widening CORS would create a surface for another site's scripts to
   *  reuse a user's valid presigned URL.
   */
  readonly uploadOrigins?: string[];
}

/** Frontend for local development. This origin is always allowed even when no deploy domain
 *  is set — developers must be able to test import on their own box. */
export const LOCAL_UPLOAD_ORIGIN = 'http://localhost:3000';

/** Days after which an abandoned staged bundle disappears.
 *
 *  Import deletes its own staging object on both success and failure. What this rule
 *  sweeps is the case where the user closed the window in between — a bundle whose
 *  upload finished but no import request arrived. Left behind, project-sized objects
 *  quietly pile up. */
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

    // Artifacts bucket — project artifacts (projects/*), strands sessions (sessions/*),
    // and import staging (imports/*).
    //
    // **Security warning: this bucket is not configured to protect data.** This is a choice for tearing down the lab
    // environment cleanly; use it knowing that workshop materials (customer interviews, strategy docs) collect here.
    // - Deleting the stack (`cdk destroy`) deletes the bucket and every object in it (DESTROY +
    //   autoDeleteObjects). It cannot be recovered.
    // - No versioning. Overwrites and deletes (by mistake, or by an attacker who obtained the instance role) cannot be undone.
    // - No server access logs. No bucket-side record of who read what remains.
    // For a long-lived environment, turn on `removalPolicy: cdk.RemovalPolicy.RETAIN` (remove autoDeleteObjects),
    // `versioned: true` with a noncurrent version expiration rule, and server access logs (separate log bucket).
    // If you turn on versioning, also decide on the fact that deleting a project leaves prior versions for the expiration period.
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      // PUT only. The browser does only uploads directly, and export is streamed by the
      // backend (response bodies have no nginx limit), so there is no reason to open GET.
      // `content-type` is a signed header, so it must be in the allowlist
      // (CONTENT_TYPE in backend/aipds/import_staging.py).
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
        // Also sweep the parts of multipart uploads cut off midway. Neither boto3's managed
        // transfer nor the browser's single PUT leaves parts, but if some path does leave
        // them, that is abandoned staging too.
        abortIncompleteMultipartUploadAfter:
          cdk.Duration.days(IMPORT_STAGING_EXPIRY_DAYS),
      }],
    });
    this.artifactsBucket = bucket;

    // Execution role the backend process assumes: Bedrock invoke + S3 (projects/* & sessions/*).
    // The backend takes this role via the EC2/container instance profile, or the role policy is
    // granted directly to the instance profile (host credential model, spec §2).
    const backendRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.AccountPrincipal(account),
      description: 'AI-PDS backend: Bedrock invoke + artifacts/session S3 access.',
    });
    for (const stmt of backendPolicyStatements(bucket, account)) {
      backendRole.addToPolicy(stmt);
    }

    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BackendRoleArn', { value: backendRole.roleArn });
    // Region the stack is actually deployed to (determined by env.region in bin/app.ts).
    new cdk.CfnOutput(this, 'Region', { value: this.region });
  }
}
