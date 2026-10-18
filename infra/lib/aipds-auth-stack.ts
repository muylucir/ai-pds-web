import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, CLIENT_NAME, COGNITO_ADMIN_SCOPE,
  GROUP_ADMIN, GROUP_PM, ID_TOKEN_VALIDITY_MINUTES,
  LOCAL_APP_URL, OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
  SEED_ADMIN_EMAIL, SEED_PM_EMAIL,
  callbackUrls, logoutUrls,
} from './auth-client-config';
import { seedProviderRole, seedUser } from './seed-users';

// OAuthScope has a private constructor and exposes only static constants (+custom()) —
// strings cannot be passed directly, so a mapping is needed. The string list has a single
// source, auth-client-config.ts (the callback-injecting custom resource uses it too).
const SCOPE_MAP: Record<string, cognito.OAuthScope> = {
  openid: cognito.OAuthScope.OPENID,
  email: cognito.OAuthScope.EMAIL,
  profile: cognito.OAuthScope.PROFILE,
  [COGNITO_ADMIN_SCOPE]: cognito.OAuthScope.COGNITO_ADMIN,
};

// Temporary password validity. Seed accounts are now created with a temporary password, so this
// window is effectively "how long the deployed accounts can be logged into". Cognito's default is
// 7 days; if the gap between deploy and workshop is longer than that, the seed accounts become
// unable to log in, and the only visible symptom is "the password is right but it won't let me in".
//
// The price: temporary passwords of invited accounts issued from the admin page get the same
// window — this is a pool policy, so it cannot differ per account. Invitations are handed over
// immediately by an admin, so the practical risk is limited to "a temporary password that was
// received and then left unused for 30 days".
const TEMP_PASSWORD_VALIDITY_DAYS = 30;

// The shape a deploy-time temporary password must satisfy = the pool's password policy.
//
// Why leave the check to CloudFormation: a value that violates the policy is rejected by
// `AdminSetUserPassword` with `InvalidPasswordException` and **the whole stack rolls back**. This
// pulls that failure, which happens a few minutes into the deploy, forward into parameter validation.
//
// Whitespace is not allowed (`\S`): Cognito itself accepts it, but this value is passed on a
// shell command line and copied by hand through a messenger.
const SEED_PASSWORD_PATTERN =
  '(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])'
  + '(?=.*[\\^$*.\\[\\]{}()?"!@#%&/\\\\,><\':;|_~`+=-])'
  + '\\S{8,256}';

export class AipdsAuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly hostedUiDomain: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- Temporary password for seed accounts: supplied by the deploy command ---
    //
    //   npx cdk deploy --all --parameters AipdsAuthStack:SeedPassword='...'
    //
    // Why not a source constant: a constant is committed to the repo and also stays in plaintext
    // in the CloudFormation template and stack events. `noEcho` cuts all three paths —
    // only a `Ref` remains in the template.
    //
    // No default: a default is just a hard-coded password, and it deploys silently.
    // If it is omitted, CloudFormation rejects it when the deploy starts.
    //
    // ⚠️ One remaining exposure: the AwsCustomResource provider Lambda logs the incoming event,
    // so the value is printed once in that log group (`Logging.withDataHidden()` only hides
    // API responses). This exposure is accepted because the value is a temporary password that
    // must be replaced at first login — it would not be acceptable for a permanent password.
    //
    // It need not be supplied again on redeploy: `cdk deploy`'s `--previous-parameters` defaults
    // to true. And the custom resource that sets the password has no `onUpdate`
    // (seed-users.ts), so even if the value changes, a redeploy does not revert existing accounts —
    // reissuing is handled by '비밀번호 재설정' (Reset password) on the admin page.
    const seedPassword = new cdk.CfnParameter(this, 'SeedPassword', {
      type: 'String',
      noEcho: true,
      minLength: 8,
      maxLength: 256,
      allowedPattern: SEED_PASSWORD_PATTERN,
      description:
        'Temporary password for the seeded admin/pm accounts. Must satisfy the pool '
        + 'policy (8+ chars, upper, lower, digit, symbol, no spaces). Both accounts '
        + 'are created in FORCE_CHANGE_PASSWORD state, so each user replaces this at '
        + 'first login.',
      constraintDescription:
        'at least 8 characters with an uppercase letter, a lowercase letter, a digit, '
        + 'a symbol, and no spaces',
    });

    // --- User Pool ---
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'aipds',
      // This one line is what "no self signup" actually is → CFN
      // AdminCreateUserConfig.AllowAdminCreateUserOnly: true.
      // The Hosted UI does not even render a sign-up link.
      selfSignUpEnabled: false,
      // Turning on username: true as well makes CDK synthesize AliasAttributes, so the caller
      // can specify the Username. With only { email: true } it becomes UsernameAttributes,
      // and Cognito auto-generates the username as a UUID; then the CDK custom resource cannot
      // know that value on each redeploy, and seeding becomes nondeterministic.
      // Either way, users log in with their email.
      signInAliases: { username: true, email: true },
      signInCaseSensitive: false,
      autoVerify: { email: true },
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(TEMP_PASSWORD_VALIDITY_DAYS),
      },
      // **Security warning: MFA is off.** This stack is an experimental/educational workshop sample (Note in the README)
      // and login is a single password. If a password leaks, that account is taken over as-is, and for an admin account
      // user and role management go with it. If you use it outside a workshop or handle real customer data, turn on MFA
      // — `mfa: cognito.Mfa.REQUIRED` and `mfaSecondFactor: { otp: true, sms: false }`
      // (TOTP authenticator app). Do not turn on SMS: this app has no path for sending text messages or email.
      mfa: cognito.Mfa.OFF,
      // **Security warning: no threat protection.** No `featurePlan` is given, so this is the default tier (Lite or Essentials),
      // and threat protection such as compromised-credential detection and risk-based authentication exists only in the Plus tier.
      // Hosted UI login attempts are not blocked by anything beyond Cognito's default lockout (there is no WAF in front either).
      // If you use it outside a workshop, start with `featurePlan: cognito.FeaturePlan.PLUS` and
      // `standardThreatProtectionMode: cognito.StandardThreatProtectionMode.AUDIT_ONLY`,
      // review the detections, then raise it to `FULL_FUNCTION`. Plus is billed per MAU.
      //
      // This app sends no email at all, so there is no path to deliver a self-service reset
      // code. Resets are done by an admin on the admin page.
      accountRecovery: cognito.AccountRecovery.NONE,
      // **Security warning: deleting the stack deletes the user pool too.** Accounts and roles disappear together and cannot
      // be recovered. This is a deliberate choice to tear down lab environments cleanly. For an environment meant to persist,
      // change it to `cdk.RemovalPolicy.RETAIN` and `deletionProtection: true`.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Role = group. This is the only source of roles (no custom attributes). ---
    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: GROUP_ADMIN,
      description: 'AI-PDS 관리자 — PM 권한 + 사용자 관리',
      precedence: 0,
    });
    new cognito.CfnUserPoolGroup(this, 'PmGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: GROUP_PM,
      description: 'AI-PDS PM — 프로젝트 전체 접근, 사용자 관리 제외',
      precedence: 10,
    });

    // --- Hosted UI v2 (managed login) ---
    // The domain prefix must be unique within the account and region.
    const domainPrefix = `aipds-${account}-${region}`;
    const domain = this.userPool.addDomain('HostedUi', {
      cognitoDomain: { domainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    this.hostedUiDomain = `${domainPrefix}.auth.${region}.amazoncognito.com`;

    // --- App client ---
    // confidential (has a secret): the code exchange is server-side (Next route handler), so the
    // secret can be stored safely, and having one blocks code interception by someone who stole only the client_id.
    // Callbacks are localhost only — the real CloudFront domain is appended by HostingStack (§3.5).
    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: CLIENT_NAME,
      generateSecret: true,
      authFlows: { userSrp: false, userPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false },
        scopes: OAUTH_SCOPES.map((s) => SCOPE_MAP[s]),
        callbackUrls: callbackUrls([LOCAL_APP_URL]),
        logoutUrls: logoutUrls([LOCAL_APP_URL]),
      },
      // These values have a single source, auth-client-config.ts — HostingStack's
      // UpdateUserPoolClient resend (PUT semantics) must write the same values again
      // so that the validity periods are not reset on every redeploy.
      accessTokenValidity: cdk.Duration.minutes(ACCESS_TOKEN_VALIDITY_MINUTES),
      idTokenValidity: cdk.Duration.minutes(ID_TOKEN_VALIDITY_MINUTES),
      refreshTokenValidity: cdk.Duration.minutes(REFRESH_TOKEN_VALIDITY_MINUTES),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

    // v2 renders correctly only if a branding style record exists (the console does this automatically).
    // Without it, the login page comes up broken.
    //
    // clientId is optional in the CDK types but required by the Cognito API — branding
    // styles are attached per app client, not per user pool. If it is not passed,
    // synth and unit tests pass and the real deploy dies with "Value null at 'clientId' failed to
    // satisfy constraint" (measured: AipdsAuthStack ROLLBACK).
    // That is why this block must come after the client is created.
    const branding = new cognito.CfnManagedLoginBranding(this, 'Branding', {
      userPoolId: this.userPool.userPoolId,
      clientId: this.userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });
    // The domain must exist first so that the target for branding (managed login) exists.
    branding.node.addDependency(domain);

    // --- Seed accounts: a single cdk deploy must make login possible ---
    //
    // 'Can log in' does not mean 'the password is finalized'. Both accounts are created with a
    // temporary password (FORCE_CHANGE_PASSWORD), and on first login the user sets their own
    // password on the screen the Hosted UI shows — same as an invited account.
    //
    // The role is created once here and shared by both seeds. Creating permissions per seed races
    // with IAM eventual consistency, and losing that race actually rolled back the first deploy —
    // see the `seedProviderRole` comment in seed-users.ts for the evidence.
    const seedRole = seedProviderRole(this, this.userPool);
    seedUser(this, 'SeedAdmin', {
      userPool: this.userPool,
      email: SEED_ADMIN_EMAIL,
      group: GROUP_ADMIN,
      password: seedPassword.valueAsString,
      role: seedRole,
    });
    seedUser(this, 'SeedPm', {
      userPool: this.userPool,
      email: SEED_PM_EMAIL,
      group: GROUP_PM,
      password: seedPassword.valueAsString,
      role: seedRole,
    });

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, 'HostedUiDomain', { value: this.hostedUiDomain });
    // The client secret is not output — EC2 looks it up at boot with
    // describe-user-pool-client (spec §3.4).
  }
}
