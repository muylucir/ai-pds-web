# AI-PDS Infra (CDK, `ap-northeast-2` / Seoul by default)

[한국어](README.ko.md) | **English**

How to deploy is in the root [`README.md`](../README.md); how to operate a deployment is in the app's
`/manual`. This document is about **why the stacks are shaped the way they are** — the decisions that
break silently, with no error, when someone misses them.

## Six stacks, in two groups

| Stack | What it creates |
|---|---|
| `AipdsDrillStack` | S3 artifact bucket + backend execution role (Bedrock invoke + S3) |
| `AipdsAuthStack` | Cognito User Pool + Hosted UI v2 + `admin`/`pm` groups + two seed accounts |
| `AipdsHostingStack` | VPC + EC2 (AL2023 x86_64, m7i.2xlarge, 100 GB encrypted EBS) + CloudFront |
| `AipdsPreviewStack` | The preview-only CloudFront distribution |
| `AipdsAgentCredsStack` | The Bedrock-only `AgentRole` for sandboxed processes |
| `AipdsUploadCorsStack` | The instance role's permission to read and write the bucket's CORS |

The **first three** reference each other (`bin/app.ts` passes the bucket and User Pool into the hosting
stack), so they deploy together with `--all` and CDK decides the order.

The **last three** exist because of one fact: **a HostingStack deploy can replace the EC2 instance.**
The AMI is not pinned (`latestAmazonLinux2023()`) and user-data is part of the template
(`userDataCausesReplacement`), so anything that changes HostingStack risks a new instance. Features
the instance turns on after boot therefore live in separate stacks that only *reference* what
HostingStack created (the instance role, the origin secret, the EIP's DNS name) and never modify it.

- **How their values reach the instance.** Not through user-data: user-data referencing their outputs
  is a cycle (they reference the instance role), and breaking it by editing user-data replaces the
  instance. Instead the stacks write SSM parameters (`lib/instance-params.ts`) and the instance reads
  them at run time — `aipds-harden sync` at boot and every 2 minutes after. A missing parameter leaves
  the feature off; it never turns a running one off.
- **Two ways to deploy them** (`lib/sandbox-stacks.ts`). In a new environment they take HostingStack's
  values as cross-stack references, so one `cdk deploy --all` does everything. In a running
  environment the four values are pinned with env (`AIPDS_INSTANCE_ROLE_ARN`,
  `AIPDS_PREVIEW_ORIGIN_DNS`, `AIPDS_ORIGIN_VERIFY_SECRET_ARN`, `AIPDS_ARTIFACTS_BUCKET` — all or none):
  a cross-stack reference would make `cdk deploy AipdsAgentCredsStack` deploy HostingStack too.
- **Protecting a running HostingStack.** A stack policy that denies `Update:Replace` and
  `Update:Delete`, plus termination protection, turns an accidental replacement into a failed,
  rolled-back deploy. The commands are in `/manual` (*Getting a fresh instance*).

## What the separate stacks isolate

**Prototypes are served from another origin.** A prototype is code the build agent wrote. On the app's
origin, a signed-in viewer's session cookie would reach the prototype's server through the `/api`
proxy, and the prototype's own JavaScript could call the app API with the viewer's rights. The preview
distribution passes only `/api/proto/*` (a CloudFront Function answers 404 to everything else) and adds
a second secret header, `X-Preview-Verify`, which the backend checks before serving a prototype
(`backend/aipds/preview_surface.py`). `*.cloudfront.net` is on the public suffix list, so the two
distributions are different sites to the browser.

**Agents and prototypes run as other users with other credentials.** The Discovery and build agents
read uploaded documents and prototypes run agent-written code and its npm dependencies, so neither runs
as the backend. `scripts/aipds-launch` (root, via sudo) starts them as `aipds-agent` / `aipds-proto` in
systemd units that see only their own project's tree, with IMDS blocked per unit. They get short-lived
`AgentRole` credentials from a loopback endpoint (`backend/aipds/credentials.py`), and that role can
invoke Bedrock and nothing else. If the launcher is switched on but its startup check fails, the backend
refuses agent turns and hosting rather than falling back to running directly.

**The instance keeps its own origin in the bucket's CORS.** Project import has the browser PUT the
bundle straight to S3, so the bucket's CORS must allow the app origin — HostingStack's CloudFront. The
bucket (DrillStack) cannot reference that without a cycle, so `aipds-harden sync` adds `APP_BASE_URL` to
the PUT rule whenever it is missing, with the permission `AipdsUploadCorsStack` grants. A DrillStack
redeploy that rewrites the CORS is repaired within minutes.

## The backend role

**Six bucket prefixes** (`lib/backend-permissions.ts`): `projects/*`, `sessions/*`, `surveys/*`,
`models/*`, `design/*`, `imports/*`. The permission is an allowlist, and a missing prefix shows up as a
generic error on screen with a single `AccessDenied` line in the backend log. Four of them sit
**outside** the project prefix for a reason: a survey token is looked up before anyone knows its project,
the model catalog and the design profile exist with no project at all, and import staging must not be
mistaken for project data by the project scan or by a project's `delete_prefix`.

**Bedrock invoke is a wildcard** over `global.anthropic.claude-*` inference profiles and the matching
foundation models. Administrators add models from `/admin/models`; an explicit list would let them
register a model that then fails on its first turn. The roles also carry
`aws-marketplace:Subscribe`/`Unsubscribe`/`ViewSubscriptions`, conditioned on
`aws:CalledViaLast = bedrock.amazonaws.com`: Bedrock creates a model's Marketplace subscription on the
account's first call, and the condition means the subscription can only happen through a Bedrock call.

## What gets deployed: pushed `main`, not your working tree

user-data clones the public repo, moves onto `origin/main` as of boot, then builds and starts the
backend and frontend (`lib/deploy-source.ts`).

- **A clone only takes tracked files.** Uploading the working tree would also carry gitignored files —
  a development `.claude/CLAUDE.md`, for instance, becomes an *ancestor* of the agent's cwd and is
  injected into every turn. `test/deployed-tree.assert.ts` pins the invariant with `git ls-files`.
- **No commit SHA is pinned.** The deployer is never asked whether they pushed; the price is that
  `cdk deploy` does not update code, since byte-identical user-data does not replace the instance.
  `aipds-update` on the instance does (its own comments explain its steps).

## AipdsAuthStack

- **Self-signup blocked** — `selfSignUpEnabled: false` renders as
  `AdminCreateUserConfig.AllowAdminCreateUserOnly: true`. Accounts exist only by invitation.
- **Roles** are the `admin` (precedence 0) and `pm` (precedence 10) groups, not a custom attribute.
- **username == email** — `signInAliases: { username: true, email: true }` becomes
  `AliasAttributes: ['email']`, which lets the caller choose the Username. `{ email: true }` alone
  becomes `UsernameAttributes`, and Cognito then generates a UUID the seeding custom resource cannot
  know across redeployments.
- **Seed accounts** — `AdminCreateUser` (SUPPRESS) → `AdminSetUserPassword` (`Permanent: false`) →
  `AdminAddUserToGroup` (`lib/seed-users.ts`). The accounts stay in `FORCE_CHANGE_PASSWORD`, so the Hosted
  UI demands a new password at first login. The custom resource has no `onUpdate`, so a redeploy never
  overwrites what a user chose.

**One source of truth for the app client.** Token validity, auth flows and the client name live in
`lib/auth-client-config.ts`. AuthStack creates the app client with them and HostingStack resends them
(below); if the two disagreed, every redeploy would silently reset them.

**The callback-URL circular dependency.** Cognito accepts only exact callback URLs, and the real one
depends on HostingStack's CloudFront domain. AuthStack deploys with localhost callbacks, and HostingStack
registers the real domain with `UpdateUserPoolClient` at the end of its deployment. ⚠️ **That API has PUT
semantics** — any field left out is cleared — so the call resends the entire client config. **A field
added to AuthStack's app client must be mirrored in HostingStack's resend**, or the next deploy wipes it.
`test/hosting-stack.assert.ts` compares the two.

**The client secret** is not a CfnOutput. The instance reads it at boot with
`describe-user-pool-client`; copying it into Secrets Manager would route a Cognito-generated value
through CloudFormation in plaintext.

**The seed password** is a required `NoEcho` parameter, not a source constant — a constant would be
committed, sit in plaintext in the template and stack events, and let a redeploy reset the accounts.
`allowedPattern` enforces the pool policy before the deployment starts; otherwise
`AdminSetUserPassword` rejects the value minutes in and rolls the whole stack back. Temporary passwords
last 30 days (`TEMP_PASSWORD_VALIDITY_DAYS`) instead of Cognito's 7, so seed accounts survive the gap
between deployment and a workshop. One exposure remains: the `AwsCustomResource` provider Lambda logs its
incoming event once. That is acceptable only because the value must be replaced at first login.

**Deletion** — the User Pool is `RemovalPolicy.DESTROY`; `cdk destroy --all` removes every account.

## Origin protection

EC2 accepts port 80 only from the CloudFront origin-facing managed prefix list, and nginx checks the
secret `X-Origin-Verify` header CloudFront attaches. There are two layers because the prefix list only
proves "came from *a* CloudFront distribution" — someone else's is in it too. No SSH port is open;
access is `aws ssm start-session`.

## Region lookup and cdk.context.json

`CDK_DEPLOY_REGION` overrides Seoul. `PrefixList.fromLookup` resolves the region's prefix list ID, so no
code changes, but the hosting stack's first synth needs account credentials. The result is cached in
`cdk.context.json`, which is gitignored: its key contains the account ID.

## What the tests guard

```bash
npm ci
npm test     # no credentials needed — pure functions + assertions on the synthesized templates
```

| File | What it guards |
|---|---|
| `user-data.assert.ts` | The boot script — nginx-vs-shell escaping, non-root execution (Claude Code refuses `bypassPermissions` at euid 0), proxy buffers large enough for JWT cookies, two distinct config dirs, `aipds-update` shipping, and `aipds-harden boot` running before the services start |
| `hosting-stack.assert.ts` | Prefix-list-only security group (no SSH), EC2/EBS/EIP/instance role, CloudFront's origin header and HTTPS redirect, and the **app client drift check** |
| `auth-stack.assert.ts` | No self-signup, alias username, groups, managed login v2, code-only client, the three seeding steps |
| `auth-client-config.assert.ts` | Token validity that **outlasts one prototype build**, seed and group constants, callback/logout URLs |
| `preview-stack.assert.ts` | Only prototype paths reach the origin, both secret headers are attached, the instance role can read the preview secret, and no instance-side resource is created |
| `agent-creds-stack.assert.ts` | `AgentRole` is assumable only by the instance role and can do nothing but invoke Bedrock (plus the Marketplace subscription through Bedrock) |
| `sandbox-stacks.assert.ts` | The two deploy modes — references in a new environment, **no dependency on HostingStack** when pinned |
| `instance-params.assert.ts` | The SSM parameter names the stacks write match the ones `aipds-harden` reads. A mismatch passes every other test and boots a replaced instance with isolation off |
| `deployed-tree.assert.ts` | What must and must not be in the tree that becomes `/opt/aipds` |
| `deploy-source.assert.ts` | The clone URL is public HTTPS, and the target is a branch, not a pinned commit |
