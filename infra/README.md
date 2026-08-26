# AI-PDS Infra (CDK, `ap-northeast-2` / Seoul by default)

[한국어](README.ko.md) | **English**

The deployment walkthrough — bootstrap, `cdk deploy`, outputs, access, changing the region, updating
the code, teardown, troubleshooting — is in the root [`README.md`](../README.md). This document is
about **why the stacks are shaped the way they are**: the decisions that break silently, with no
error, when someone misses them.

## The three stacks

| Stack | What it creates |
|---|---|
| `AipdsDrillStack` | S3 artifact bucket (`projects/*` + `sessions/*` + `surveys/*` + `models/*`) + backend execution role (Bedrock invoke + S3) |
| `AipdsAuthStack` | Cognito User Pool + Hosted UI v2 (managed login) + 2 role groups (`admin`/`pm`) + 2 seed accounts |
| `AipdsHostingStack` | VPC + EC2 (AL2023 x86_64, m7i.2xlarge, 100 GB encrypted EBS) + CloudFront |

The three reference each other, so **deploy them together with `--all`** (`bin/app.ts` passes the
bucket and User Pool references into the hosting stack). CDK decides the order.

**Why there are four bucket prefixes** is written out in `lib/backend-permissions.ts`. They hold
project data, session transcripts, surveys, and the model catalog — and the last two have to live
**outside** the project prefix (a survey token is looked up before anyone knows which project it
belongs to, and the model catalog is read when no project exists yet). The reasoning in that comment
comes from a real bug: `surveys/*` was missing from the list and every survey creation returned 500.
On screen it was a generic error; the cause was a single `AccessDenied` line in the backend log.

Prototype builds (a Claude Agent SDK agent) run **inside the backend process** — there is no separate
VM or MicroVM layer.

## What gets deployed: pushed `main`, not your working tree

user-data clones the public repo, moves onto the latest `origin/main` commit as of boot, then builds
and starts the backend and frontend. The reasoning is spelled out in `lib/deploy-source.ts`; it comes
down to two things.

- **A clone only takes tracked files.** The previous CDK asset (zip) approach shipped gitignored
  files too, which had to be corrected with a human-maintained exclusion list. What fell off that
  list caused two incidents: the repo's development `.claude/CLAUDE.md` became an **ancestor** of the
  agent's cwd and injected one Korean line into every turn of an English project, and a dev box's
  `proto-type/` shipped, making a prototype nobody had built look "build complete".
  `test/deployed-tree.assert.ts` pins that invariant using `git ls-files`.
- **No commit SHA is pinned.** That means the deployer is never asked "did you push this commit?",
  but the price is that **`cdk deploy` is not how you update code** — if the user-data string is
  byte-identical, CloudFormation does not replace the instance. Updating code is the job of
  `aipds-update`, which is installed at boot (see "Updating the code" in the root README).

## AipdsAuthStack

- **Self-signup blocked** — `selfSignUpEnabled: false` renders as CFN
  `AdminCreateUserConfig.AllowAdminCreateUserOnly: true`. The Hosted UI shows no sign-up link, and
  new accounts exist only by invitation from `/admin/users`.
- **Roles** — `admin` (precedence 0) / `pm` (precedence 10) groups. Role is not a custom attribute.
- **username == email** — `signInAliases: { username: true, email: true }` becomes CFN
  `AliasAttributes: ['email']`, which lets the caller choose the Username. With `{ email: true }`
  alone it becomes `UsernameAttributes` and Cognito auto-generates a UUID username — and then the CDK
  custom resource cannot know that value across redeployments, making seeding non-deterministic.
- **Seed accounts** — `AdminCreateUser` (SUPPRESS) → `AdminSetUserPassword` (Permanent) →
  `AdminAddUserToGroup`. The `CfnUserPoolUser` L1 construct cannot set a final password, so it would
  demand a change on every first login; hence the custom resource.

### One source of truth for the app client config

Token validity (`ACCESS_TOKEN_VALIDITY_MINUTES` / `ID_TOKEN_VALIDITY_MINUTES` /
`REFRESH_TOKEN_VALIDITY_MINUTES`), the permitted auth flows (`EXPLICIT_AUTH_FLOWS`), and the client
name (`CLIENT_NAME`) live in `lib/auth-client-config.ts` alongside the seed-account constants. Both
writers must use the same values: AuthStack when it creates the app client, and HostingStack when it
resends the config with `UpdateUserPoolClient` at the end of the deployment (see below). If the two
disagree, every redeployment silently resets validity and auth flows.

### The callback-URL circular dependency

Cognito only accepts exact-match callback URLs (no wildcards), and the real URL depends on the
CloudFront domain that HostingStack creates. So AuthStack deploys with localhost callbacks only, and
HostingStack registers the real domain via `UpdateUserPoolClient` at the end of its deployment.

⚠️ **That API has PUT semantics** — it clears any field you do not specify. So the call resends the
*entire* client config (callback/logout URLs, OAuth scopes, token validity, auth flows), not just the
callbacks. Because the values come from `lib/auth-client-config.ts` and nowhere else, they cannot
drift from AuthStack. **If you add a field to AuthStack's app client, you must mirror it in
HostingStack's resend** — miss it and that field is silently wiped on the next deployment. A drift
detection test (`test/hosting-stack.assert.ts`) compares the two definitions so CI catches what a
human misses.

### The client secret

It is not exported as a CfnOutput. The EC2 instance reads it at boot with
`aws cognito-idp describe-user-pool-client` — making a Secrets Manager copy would mean routing a
Cognito-generated value through CloudFormation, which leaves it in the template in plaintext. The
price is the `cognito-idp:DescribeUserPoolClient` permission on the instance role.

### The seed password

It is a **required** deploy-time parameter (`AipdsAuthStack:SeedPassword`, no default):

```bash
npx cdk deploy --all --require-approval never \
  --parameters AipdsAuthStack:SeedPassword='<temporary-password>'
```

An `allowedPattern` enforces the pool policy (8+ characters with an uppercase letter, a lowercase
letter, a digit, a symbol, and no spaces) before the deployment starts. Without that check
`AdminSetUserPassword` rejects the value with `InvalidPasswordException` and **rolls the whole stack
back** — minutes into the deployment.

Both seed accounts receive it as a **temporary** password (`Permanent: false`), so they stay in
`FORCE_CHANGE_PASSWORD` state and the Hosted UI requires a new password at first login. The custom
resource that seeds the password has **no** `onUpdate`, so a redeployment does not overwrite what the
user chose. Reissuing goes through **Reset password** in `/admin/users`.

The temporary password is valid for 30 days (`TEMP_PASSWORD_VALIDITY_DAYS`). Cognito's default of 7
days makes the seed accounts unusable when more time passes between deployment and the workshop, and
the only symptom is "the password is right but it will not let me in". It is a pool policy, so
invited accounts' temporary passwords share the window.

**Why not a source constant.** A constant gets committed to the repository, stays in plaintext in the
CloudFormation template and stack events, and lets a redeployment reset the accounts to it. One
`NoEcho` parameter closes all three paths — the template carries only a `Ref`, and the only party that
knows the value is whoever ran the deployment. It does not conflict with the deploy-in-one-command
requirement either: it is one more flag on one command, and `--previous-parameters` defaults to true,
so a redeployment does not need it again.

**One exposure remains.** The `AwsCustomResource` provider Lambda logs its incoming event, so the
value is printed once into that log group (`Logging.withDataHidden()` only hides API responses). That
is acceptable because the value is a temporary password that must be replaced at first login — it
would not be acceptable for a standing one.

### Deletion

On `cdk destroy --all` the User Pool is `RemovalPolicy.DESTROY`, so **every user account disappears
with it.**

## Origin protection

EC2 accepts port 80 only from the CloudFront origin-facing managed prefix list (looked up
automatically for the deployment region), and nginx verifies the secret `X-Origin-Verify` header
CloudFront attaches. No SSH port is opened — access is `aws ssm start-session`. There are two layers
because the prefix list only narrows traffic down to "came from CloudFront": **someone else's**
CloudFront distribution is in that list too, so only the header distinguishes our distribution.

## Region lookup and cdk.context.json

The default is Seoul (`ap-northeast-2`), overridden with `CDK_DEPLOY_REGION`. Prefix list IDs differ
per region, but `PrefixList.fromLookup` resolves the deployment region's ID automatically, so no code
changes are needed. The price is that **the hosting stack's first synth/deploy needs account
credentials** (`npx cdk synth AipdsDrillStack` does not).

The lookup result is cached in `cdk.context.json`, which is **not committed** (gitignored) — the
entry key contains the account ID, so the cache is invalid for any other account, and it is
regenerated with the same value whenever credentials are present. That is why the first synth in a
fresh clone needs credentials.

## What the tests guard

```bash
npm ci
npm test     # no credentials needed — pure functions + assertions on the synthesized template
```

Six assertion files, each aimed at a regression **you cannot see by looking**:

| File | What it guards |
|---|---|
| `user-data.assert.ts` | Every element of the boot script — nginx-var vs shell-var escaping, non-root execution (Claude Code refuses `bypassPermissions` at euid 0), the proxy buffer sizes that JWT cookies have to fit, the two config dirs being distinct paths, the two context switches, and that `aipds-update` ships |
| `hosting-stack.assert.ts` | The SG being prefix-list-only (no SSH), EC2/EBS/EIP/instance role, CloudFront's origin header and HTTPS redirect, and the **app client drift detection** described above |
| `auth-stack.assert.ts` | No-self-signup, alias username, groups, managed login v2, code-only client, and the pairing of the three seed-account steps |
| `auth-client-config.assert.ts` | That token validity **outlasts one prototype build** (shorter and the session expires mid-build), plus the seed/group constants and callback/logout URL derivation |
| `deployed-tree.assert.ts` | What must not be in the tree that becomes `/opt/aipds` (the dev-only `.claude/`, build output, session state) and what must be (rules, both language directives, both config dirs, the lockfile) |
| `deploy-source.assert.ts` | That the clone URL is public HTTPS, and that the deploy target is a branch rather than a pinned commit |
