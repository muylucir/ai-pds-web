import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import { backendPolicyStatements, MODEL } from './backend-permissions';
import { DEPLOY_BRANCH, REPO_URL } from './deploy-source';
import { renderUserData } from './user-data';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, CLIENT_NAME, EXPLICIT_AUTH_FLOWS, ID_TOKEN_VALIDITY_MINUTES,
  LOCAL_APP_URL, OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
  callbackUrls, logoutUrls,
} from './auth-client-config';

export interface HostingStackProps extends cdk.StackProps {
  artifactsBucket: s3.IBucket;
  // For test injection. If unset, the CloudFront origin-facing prefix list for the deploy
  // region is looked up automatically with fromLookup.
  cfPrefixListId?: string;
  // Auth. Takes the pool/client AuthStack created, (1) plants them in EC2 as env and
  // (2) registers the CloudFront domain as a callback URL.
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  hostedUiDomain: string;
}

export class AipdsHostingStack extends cdk.Stack {
  /** Instance role. Separate stacks (AgentCreds, Preview) attach permissions to it. */
  public readonly instanceRole: iam.IRole;
  /** The `X-Origin-Verify` secret. The preview distribution attaches the same value. */
  public readonly originVerifySecret: secretsmanager.ISecret;
  /** Public DNS name of the EIP. A CloudFront origin does not accept an IP. */
  public readonly originDnsName: string;

  constructor(scope: Construct, id: string, props: HostingStackProps) {
    super(scope, id, props);

    // --- VPC: public subnets only, no NAT (zero cost). ---
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    // --- CloudFront origin-facing prefix list (region auto-detected) ---
    const cfPrefixListId =
      props.cfPrefixListId ??
      ec2.PrefixList.fromLookup(this, 'CfOriginFacing', {
        prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
      }).prefixListId;

    // --- SG: port 80 only, only from the CloudFront prefix list. No SSH. ---
    const sg = new ec2.SecurityGroup(this, 'InstanceSg', {
      vpc,
      allowAllOutbound: true, // package installs · Bedrock · S3
      // ASCII only: EC2 rejects non-ASCII in GroupDescription ("Character sets
      // beyond ASCII are not supported"). A single em dash rolls the stack back (observed).
      description: 'AI-PDS EC2: inbound 80 from CloudFront prefix list only.',
    });
    sg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixListId),
      ec2.Port.tcp(80),
      'CloudFront origin-facing only',
    );

    // --- Secret header value (32 alphanumeric characters) ---
    const headerSecret = new secretsmanager.Secret(this, 'OriginVerifyHeader', {
      description: 'X-Origin-Verify shared secret (CloudFront custom header <-> nginx).',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
        // A single scalar secret (the string as is) — not JSON.
      },
    });

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- Instance role: common backend permissions + secret read + SSM access ---
    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
      description: 'AI-PDS EC2: Bedrock + artifacts S3 + read header secret.',
    });
    for (const stmt of backendPolicyStatements(props.artifactsBucket, account)) {
      role.addToPolicy(stmt);
    }
    headerSecret.grantRead(role);
    // Two groups of Cognito permissions.
    //
    // 1) DescribeUserPoolClient — at boot, the client secret is read straight from Cognito
    //    (§3.4). That choice is so it is not left in plaintext in the template, and the price is
    //    this permission.
    //
    // 2) Admin* / ListUsers* — used by /admin/users (inviting users, changing roles, resetting
    //    passwords, disabling, deleting). Observed deploy bug: without this group, login
    //    worked but only /admin/users returned 502, and only the backend log showed
    //    "cognito call failed (AccessDeniedException)".
    //    The list must match the _call() invocations in backend/aipds/auth/cognito.py 1:1
    //    — if even one is missing, only that feature returns 502 and the cause is not visible
    //    on screen. Do not collapse it into cognito-idp:* (least privilege). So the two places do not
    //    drift, test/hosting-stack.assert.ts pins the list.
    role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'cognito-idp:DescribeUserPoolClient',
        'cognito-idp:ListUsers',
        'cognito-idp:ListUsersInGroup',
        'cognito-idp:AdminCreateUser',
        'cognito-idp:AdminDeleteUser',
        'cognito-idp:AdminDisableUser',
        'cognito-idp:AdminEnableUser',
        'cognito-idp:AdminSetUserPassword',
        'cognito-idp:AdminAddUserToGroup',
        'cognito-idp:AdminRemoveUserFromGroup',
        'cognito-idp:AdminListGroupsForUser',
      ],
      resources: [props.userPool.userPoolArn],
    }));

    // --- Code to deploy: a specific commit of the repo ---
    //
    // Previously the repo root was uploaded as a CDK asset (zip) and EC2 downloaded it from S3.
    // Two things that approach left behind are the reason for this change (full reasoning in lib/deploy-source.ts):
    //
    // - The asset **carries even gitignored files.** That needed a correction list called
    //   app-asset-excludes.json, and things missing from that list caused two incidents:
    //   the development `.claude/CLAUDE.md` shipped to /opt/aipds/.claude/, became an **ancestor**
    //   of the agent's cwd, and put one Korean line into an English project's context on every
    //   turn (d94aaa1); and the dev box's `proto-type/` shipped, so a prototype nobody
    //   built showed as "build complete".
    //   clone fetches only tracked files, so this **class** of failure disappears.
    //   That invariant (no unintended CLAUDE.md in the app tree) is now
    //   guarded by test/deployed-tree.assert.ts using the list of tracked files.
    // - What got deployed was the **working tree**, not a commit. Now uncommitted changes
    //   are not deployed — the instance clones only what was pushed.
    //
    // What to clone is decided by branch only (no SHA baked in). So this
    // stack does not call git at deploy time, and **user-data is identical regardless of
    // code changes** — that is, cdk deploy neither replaces the instance nor updates the
    // code. Code updates are done by aipds-update on the instance (installed by
    // user-data.ts). The reasoning for this choice is in lib/deploy-source.ts.

    // --- user-data ---
    // The CloudFront domain is created below, but user-data is needed now.
    // Lazy.string defers resolution to the end of synthesis to break the cycle — in the CFN template
    // it lands as an Fn::GetAtt reference.
    let distributionDomain: string | undefined;
    const appUrlToken = cdk.Lazy.string({
      produce: () => `https://${distributionDomain ?? ''}`,
    });

    const userData = ec2.UserData.custom(
      renderUserData({
        region,
        bucketName: props.artifactsBucket.bucketName,
        model: MODEL,
        secretArn: headerSecret.secretArn,
        repoUrl: REPO_URL,
        branch: DEPLOY_BRANCH,
        userPoolId: props.userPool.userPoolId,
        userPoolClientId: props.userPoolClient.userPoolClientId,
        hostedUiDomain: props.hostedUiDomain,
        appUrl: appUrlToken,
      }),
    );

    // --- Instance (AL2023 x86_64, IMDSv2 enforced) ---
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      // Prototype builds moved onto this box: each session keeps a claude subprocess
      // (~300-500MB) resident and next build peaks at 2GB. No Graviton
      // (the SDK's bundled binary is x86-64).
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.M7I, ec2.InstanceSize.XLARGE2),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      securityGroup: sg,
      role,
      userData,
      requireImdsv2: true,
      userDataCausesReplacement: true, // clean re-bootstrap when the asset (code) changes
      blockDevices: [{
        deviceName: '/dev/xvda',
        // Each prototype keeps node_modules resident (observed ~23MB each, but leave headroom).
        volume: ec2.BlockDeviceVolume.ebs(100, { encrypted: true }),
      }],
    });

    // --- Fixed EIP (CloudFront origin domain unchanged across redeploys) ---
    const eip = new ec2.CfnEIP(this, 'Eip', { domain: 'vpc' });
    new ec2.CfnEIPAssociation(this, 'EipAssoc', {
      allocationId: eip.attrAllocationId,
      instanceId: instance.instanceId,
    });

    // EIP IP -> public DNS name (a CloudFront origin only accepts a domain; no IP).
    //   ec2-<a-b-c-d>.<region>.compute.amazonaws.com  (compute-1 for us-east-1)
    const computeDomain =
      region === 'us-east-1' ? 'compute-1.amazonaws.com' : `${region}.compute.amazonaws.com`;
    const originDnsName =
      `ec2-${cdk.Fn.join('-', cdk.Fn.split('.', eip.attrPublicIp))}.${computeDomain}`;
    this.instanceRole = role;
    this.originVerifySecret = headerSecret;
    this.originDnsName = originDnsName;

    // **Security warning: the CloudFront → origin leg is not encrypted (HTTP_ONLY, port 80).** Browser
    // → CloudFront is HTTPS, but the request CloudFront sends to the EC2 public DNS is plaintext and carries
    // the session cookies (Cognito tokens) and the secret header below. The security group accepts only the
    // CloudFront origin ranges and nginx checks the secret header, so direct access is blocked, but eavesdropping on the path is not.
    // This constraint exists because a public certificate cannot be issued for an EC2 public DNS name. If used outside a workshop, switch to
    // one of two: a custom domain + certificate with the origin at HTTPS_ONLY (443), or move the instance to a
    // private subnet and use a CloudFront VPC origin. Deploying this stack can replace the EC2 instance
    // (AMI not pinned), so do it during a planned replacement. The same goes for the preview distribution (aipds-preview-stack.ts).
    // This is the choice that lets this sample deploy without a domain — README "CloudFront → instance is HTTP by
    // design" covers the scope, mitigations and how to switch.
    //
    // Secret header value: injected via a CFN dynamic reference ({{resolve:secretsmanager:...}}).
    // CloudFormation resolves it at deploy time → no plaintext is left in the template.
    const origin = new origins.HttpOrigin(originDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      readTimeout: cdk.Duration.seconds(60), // headroom for SSE (backend ping 15s)
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        'X-Origin-Verify': headerSecret.secretValue.unsafeUnwrap(),
      },
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      // ASCII only, for the same reason as the SG above (CloudFront may accept non-ASCII,
      // but there is no reason to leave that risk in a string meant for console display).
      comment: 'AI-PDS: CloudFront in front of EC2 (header-authenticated origin).',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
      additionalBehaviors: {
        '/_next/static/*': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED, // hashed file names → immutable
        },
      },
    });
    distributionDomain = distribution.distributionDomainName;

    // --- Callback URL injection: breaking the circular dependency ---
    //
    // Cognito only allows exact matches on callback URLs (no wildcards), and the actual URL
    // depends on the CloudFront domain this stack creates. For AuthStack to know that domain
    // it would have to reference this stack, and this stack already references AuthStack, so
    // it would be circular. The cycle is broken by updating the client at the end of the deploy.
    //
    // ⚠️ UpdateUserPoolClient has PUT semantics — it clears any field not specified.
    // So it rewrites the whole client configuration, not just the callbacks.
    // The values come only from auth-client-config.ts, so they cannot drift from AuthStack.
    // 🔒 Obligation: every time a field is added to or changed in AuthStack's client definition (the addClient call),
    // the parameters below must be changed along with it — if even one is
    // missed, that field silently resets to its default on the next deploy (e.g. token
    // validity periods, ALLOW_REFRESH_TOKEN_AUTH — fields that actually were missed once).
    const appUrls = [LOCAL_APP_URL, `https://${distribution.distributionDomainName}`];

    // onCreate and onUpdate must be the same call: if onUpdate is omitted, a redeploy does not
    // update it, so the callbacks stay stale even when the domain changes. Pulling the parameters
    // into a local constant leaves no room for the two to diverge.
    const updateClientCall = {
      service: 'CognitoIdentityServiceProvider',
      action: 'updateUserPoolClient',
      parameters: {
        UserPoolId: props.userPool.userPoolId,
        ClientId: props.userPoolClient.userPoolClientId,
        ClientName: CLIENT_NAME,
        CallbackURLs: callbackUrls(appUrls),
        LogoutURLs: logoutUrls(appUrls),
        // PUT semantics, so leaving out the fields below clears those settings —
        // they must be the same values as AuthStack's client definition.
        AllowedOAuthFlows: ['code'],
        AllowedOAuthFlowsUserPoolClient: true,
        AllowedOAuthScopes: OAUTH_SCOPES,
        SupportedIdentityProviders: ['COGNITO'],
        PreventUserExistenceErrors: 'ENABLED',
        EnableTokenRevocation: true,
        // Token validity periods + refresh auth flow. Fields missed in the first implementation —
        // without them, every redeploy resets 1h/1h/30d to Cognito defaults and
        // ALLOW_REFRESH_TOKEN_AUTH disappears, silently breaking the /api proxy's 401 refresh.
        // The constants come from auth-client-config.ts, the same source as AuthStack.
        AccessTokenValidity: ACCESS_TOKEN_VALIDITY_MINUTES,
        IdTokenValidity: ID_TOKEN_VALIDITY_MINUTES,
        RefreshTokenValidity: REFRESH_TOKEN_VALIDITY_MINUTES,
        TokenValidityUnits: {
          AccessToken: 'minutes', IdToken: 'minutes', RefreshToken: 'minutes',
        },
        ExplicitAuthFlows: EXPLICIT_AUTH_FLOWS,
      },
      physicalResourceId: cr.PhysicalResourceId.of('aipds-callback-urls'),
    };

    const clientUpdate = new cr.AwsCustomResource(this, 'RegisterCallbackUrls', {
      onCreate: updateClientCall,
      onUpdate: updateClientCall,
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: [props.userPool.userPoolArn],
      }),
      installLatestAwsSdk: false,
    });
    // It must be called after the distribution is created so the domain is settled.
    clientUpdate.node.addDependency(distribution);

    new cdk.CfnOutput(this, 'DistributionDomain', {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'EipAddress', { value: eip.attrPublicIp });
  }
}
