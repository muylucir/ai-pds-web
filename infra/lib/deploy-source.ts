// infra/lib/deploy-source.ts — where what code gets deployed is decided.
//
// Previously the repo root was uploaded as a CDK asset (zip). Two things were wrong:
//
//   1. **What got deployed was the working tree, not committed code.** Uncommitted changes went up
//      as is, so "which commit is running right now" could not be answered.
//   2. **The asset carries even gitignored files.** That needed a correction list called
//      app-asset-excludes.json, and things missing from that list actually caused two incidents
//      (the development .claude/CLAUDE.md entering the agent context as an ancestor, and
//      the dev box's proto-type/ shipping in the deployment and showing as "build complete").
//
// Since the repo went public, the instance can clone it directly. clone fetches **only tracked files**,
// so failure class 2 disappears, and since uncommitted changes are not deployed, class 1
// disappears too.
//
// **Why we went back from commit SHA pinning to a branch.** Previously, at synth time,
// `git rev-parse HEAD` got the SHA, baked it into user-data, and did `checkout --detach`. That
// approach kept demanding of the deployer "did you push this commit" — if not, cdk deploy
// succeeds and **only the EC2 boot** fails — and CDK_DEPLOY_REF, the synth-time pushed-or-not check,
// and the typo warning all existed to prevent that one mistake. Now the instance uses the **origin/main
// tip commit** at boot time: synth does not call git, and what gets deployed
// is always "the pushed main", so the mistake itself cannot happen.
//
// **The price: `cdk deploy` is not a means of updating code.** The user-data string has no SHA, so
// pushing commits leaves user-data byte-for-byte identical, and then CloudFormation does not replace the
// instance (UserData is a replacement property). So code updates are handled by `aipds-update`
// on the instance (lib/user-data.ts installs it at boot) — log in via SSM
// and a single `sudo aipds-update` pulls the latest main, rebuilds only what is needed and
// restarts the services. There is no instance replacement (5-10 minutes of 502), so it can be used even
// during a workshop, which is the practical gain of this direction.
//
// What is running is seen on the instance with `git -C /opt/aipds rev-parse HEAD`
// (the boot-time commit is also left as one line in the bootstrap log).
//
// One more price: the boot must be able to reach GitHub.

/** Public repo. HTTPS, so the instance needs no credentials. */
export const REPO_URL = 'https://github.com/muylucir/ai-pds-web.git';

/**
 * Deploy target branch. The instance at boot, and `aipds-update` when it runs, align to this
 * branch's remote tip commit.
 *
 * Do not put a commit SHA here — that would go back to a pinned deployment where `aipds-update`
 * has nothing to update. To use another branch, change this value (it is one line).
 */
export const DEPLOY_BRANCH = 'main';
