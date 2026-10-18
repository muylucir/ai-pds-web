import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';
import { usernameForEmail } from './auth-client-config';

export interface SeedUserProps {
  userPool: cognito.IUserPool;
  email: string;
  group: string;
  /**
   * The **temporary** password received at deploy time (AipdsAuthStack's `SeedPassword` parameter).
   * The user changes it at first login, so this value is not the account's permanent password.
   */
  password: string;
  /** The provider role shared by all seed calls. Created by `seedProviderRole`. */
  role: iam.IRole;
}

/** The Cognito actions needed for the three kinds of seed calls. This list is the seed's permission boundary. */
const SEED_ACTIONS = [
  'cognito-idp:AdminCreateUser',
  'cognito-idp:AdminSetUserPassword',
  'cognito-idp:AdminAddUserToGroup',
];

// Execution role for the AwsCustomResource provider Lambda. **It holds its permissions inline.**
//
// **Why not fromSdkCalls (observed 2026-08-21).** That helper creates a separate
// `AWS::IAM::Policy` per call, and CloudFormation writes it just before it is consumed (observed 16 seconds
// before). `DependsOn` guarantees only the order of API calls, not that the IAM authorization path has
// picked up the new policy, so those 16 seconds become a race against eventual consistency. The first
// creation of AipdsAuthStack died exactly that way:
//
//   01:03:06  PutRolePolicy  SeedPmGroupCustomResourcePolicy   success
//   01:03:22  AdminAddUserToGroup  pm     -> AccessDenied
//   01:03:23  AdminAddUserToGroup  admin  -> success
//
// Same role, same action, same resource, policies of the same shape attached at the same time, and one
// lost by a one-second difference. With two seed accounts there are six calls and six races, and **if even
// one loses, the whole stack rolls back** — adding seeds raises the failure probability with them.
//
// Moving it inline makes the IAM write happen at `CreateRole` time. That role must exist before the
// provider Lambda, and the Lambda must exist before any custom resource, so the resource creation
// order itself becomes the margin (40-80 seconds in the observed timeline).
//
// Since it replaces the default execution role, `AWSLambdaBasicExecutionRole` is added directly — without it
// this Lambda's logs disappear, and those logs were exactly the diagnostic path for this defect.
export function seedProviderRole(scope: Construct,
                                 userPool: cognito.IUserPool): iam.Role {
  return new iam.Role(scope, 'SeedProviderRole', {
    assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        'service-role/AWSLambdaBasicExecutionRole'),
    ],
    inlinePolicies: {
      // It must be `inlinePolicies` to render as the role resource's `Policies` property —
      // `addToPolicy` creates a separate `AWS::IAM::Policy` (DefaultPolicy) and goes back to the same
      // race as above.
      SeedCognitoAdmin: new iam.PolicyDocument({
        statements: [new iam.PolicyStatement({
          actions: SEED_ACTIONS,
          resources: [userPool.userPoolArn],
        })],
      }),
    },
  });
}

// Creates 'login-capable' accounts with a single cdk deploy.
//
// Why not CfnUserPoolUser: `AWS::Cognito::UserPoolUser` has no property at all for specifying
// a password. Cognito generates a random temporary password that nobody can know,
// so in this app, which sends no email, there would be no way to log in.
// Planting a known temporary password requires AdminSetUserPassword, which can only be called
// through a custom resource.
export function seedUser(scope: Construct, id: string, props: SeedUserProps): void {
  const { userPool, email, group, password, role } = props;
  // The Username is the local part, not the email — this pool has AliasAttributes=[email], and
  // in that case Cognito rejects email-format Usernames (observed: stack rollback).
  // See the usernameForEmail comment for the rule's rationale and the requirement to stay in sync with the backend.
  const username = usernameForEmail(email);

  // 1) Create the user. No email sent (SUPPRESS) — invitations work by the admin page showing the
  // temporary password on screen, and seed accounts' passwords are already known.
  // email_verified=true is not optional: alias (email) sign-in works only with verified
  // emails.
  const create = new cr.AwsCustomResource(scope, `${id}Create`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminCreateUser',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        MessageAction: 'SUPPRESS',
        UserAttributes: [
          { Name: 'email', Value: email },
          { Name: 'email_verified', Value: 'true' },
        ],
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-create`),
      // On redeploy the user already exists. Treating that as a failure would roll back the stack.
      ignoreErrorCodesMatching: 'UsernameExistsException',
    },
    role,
    installLatestAwsSdk: false,
  });

  // 2) Plant a **temporary** password (Permanent: false) → the account stays in
  // FORCE_CHANGE_PASSWORD, and the Hosted UI has the user set a new password at first
  // login. Exactly the same discipline as the admin page's invitation
  // (set_temp_password in backend/aipds/auth/cognito.py).
  //
  // **Having no `onUpdate` is the point of this resource.** Putting the same call on onUpdate too
  // would make a redeploy reset the password to the deploy-time value, meaning the next `cdk deploy`
  // overwrites the password the user set at first login — directly contradicting the requirement
  // that everyone has their own password. So this call runs only once, when the account is
  // first created. If a password needs to be reissued, use the admin page's
  // '비밀번호 재설정' (Reset password) (it shows a new temporary password once).
  //
  // The Username uses the same value derived above rather than step 1's response: if step 1 is
  // ignored with UsernameExistsException, the response fields are empty and getResponseField
  // breaks. This works because the pool uses AliasAttributes (caller-specified username).
  const setPassword = new cr.AwsCustomResource(scope, `${id}Password`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminSetUserPassword',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        Password: password,
        Permanent: false,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-password`),
    },
    role,
    installLatestAwsSdk: false,
  });
  setPassword.node.addDependency(create);

  // 3) Group assignment = role grant. A no-op if already a member (idempotent).
  const addToGroup = new cr.AwsCustomResource(scope, `${id}Group`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminAddUserToGroup',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    onUpdate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminAddUserToGroup',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    role,
    installLatestAwsSdk: false,
  });
  addToGroup.node.addDependency(setPassword);
}
