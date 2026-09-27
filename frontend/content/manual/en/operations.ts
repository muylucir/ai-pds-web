import type { ManualSection } from "../types";

export const operations: ManualSection = {
  id: "operations",
  title: "Install, operate, troubleshoot",
  lede: "For whoever stands AI-PDS Web up and keeps it running. If you only use it, you can skip this.",
  blocks: [
    {
      kind: "callout",
      tone: "note",
      md: `This section is for **whoever deploys AI-PDS Web into an AWS account**. If someone handed you a
running address, start at [Getting started](/manual#getting-started) instead.`,
    },
    { kind: "heading", id: "deploy", text: "Deploying" },
    {
      kind: "md",
      md: `You need Node.js 20+, administrator-level AWS credentials (it creates IAM roles, Cognito and a
VPC), and **an account that can call Claude models**.

Bedrock creates the AWS Marketplace subscription for a Claude model automatically the first time the
account calls it, and the deployed roles already hold the permissions that subscription needs. Two
things are up to you: submit the **Anthropic first-time-use form (use case details)** once in the
account (or the organization's management account), and have a Marketplace payment method on the
account. Miss either and the deployment succeeds while the first conversation fails — it is the most
common mistake.`,
    },
    {
      kind: "cmd",
      caption: "Bootstrap is only needed once per account-and-region pair",
      lines: [
        "cd infra",
        "npm ci",
        "npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2",
        "npx cdk deploy --all --require-approval never",
      ],
    },
    {
      kind: "md",
      md: `The stacks reference one another, so deploy them **together with \`--all\`**. CDK decides the
order.

| Stack | What it creates |
|---|---|
| \`AipdsDrillStack\` | The artifacts S3 bucket + the backend execution role |
| \`AipdsAuthStack\` | Cognito user pool + hosted sign-in + role groups + seed accounts |
| \`AipdsHostingStack\` | VPC + EC2 + CloudFront |
| \`AipdsPreviewStack\` | A CloudFront distribution just for prototype previews ([sharing the preview](/manual#share)) |
| \`AipdsAgentCredsStack\` | A Bedrock-only role for the sandboxed agents and prototypes ([sandbox](/manual#sandbox)) |
| \`AipdsUploadCorsStack\` | Lets the instance keep its app address in the bucket's CORS — this is what makes the upload in [project import](/manual#transfer-project) work |

It takes **15–20 minutes**. Even after \`cdk deploy\` returns, EC2 may still be building the backend
and frontend, so **a few minutes of 502 responses is normal.** Within a few minutes after that, the
instance applies the sandbox and preview settings by itself and restarts the backend once. There is
nothing to run by hand.

The address to open is the \`AipdsHostingStack.DistributionDomain\` output; prototype share links go
out on the \`AipdsPreviewStack.PreviewOrigin\` address.`,
    },
    { kind: "heading", id: "migrate", text: "Moving from an existing deployment" },
    {
      kind: "md",
      md: `Renaming the stacks makes CloudFormation lose its link to the existing ones. You end up with
3 new stacks while the 3 existing ones remain.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**0. Once this change is on \`main\`, do not run \`sudo aipds-update\` on the existing
instance.** Its systemd units and tree paths still carry the old names, so the update script aborts
partway through — and that instance is exactly the one that must keep serving in-flight survey links
until the old stacks are deleted in step 9 below. Leave it frozen until then.`,
    },
    {
      kind: "steps",
      items: [
        "`cdk deploy AipdsDrillStack AipdsAuthStack` — the new bucket and user pool",
        "`aws s3 sync s3://<existing bucket> s3://<new bucket>` — moves the artifacts over. The key prefixes carry no product name, so the layout comes up unchanged. After it finishes, compare object counts: run `aws s3api list-objects-v2 --bucket <new bucket> --query 'length(Contents)'` and the same command against the existing bucket — the deletion below is irreversible, so seeing project cards on screen is not enough to catch a partially failed sync",
        "Move the Discovery transcript prefixes. Transcripts are stored under a UUID derived from the project id, and this rename changed that derivation — without this step **every project's Discovery conversation starts as an empty session** (artifacts, surveys and responses are unaffected). The loop below reads whatever directory is already there and moves it to the new name, so it is safe to run more than once:\n\n```bash\nfor pid in $(aws s3 ls s3://<new bucket>/projects/ | awk '{print $2}' | tr -d /); do\n  base=\"s3://<new bucket>/projects/$pid/discovery/transcript\"\n  cur=$(aws s3 ls \"$base/\" 2>/dev/null | awk '{print $2}' | tr -d / | head -1)\n  want=$(python3 -c \"import uuid,sys;print(uuid.uuid5(uuid.NAMESPACE_URL,'aipds:'+sys.argv[1]))\" \"$pid\")\n  if [ -n \"$cur\" ] && [ \"$cur\" != \"$want\" ]; then\n    aws s3 mv \"$base/$cur/\" \"$base/$want/\" --recursive\n  fi\ndone\n```",
        "`cdk deploy AipdsHostingStack` — the new EC2 instance and CloudFront",
        "Confirm you can sign in at the new address (`admin@aipds.local`)",
        "Check the project cards — specs and surveys are intact, and prototypes are **unbuilt**",
        "Rebuild whichever prototypes you need. Build output lived only on the instance disk, so it does not come along",
        "Once every in-flight survey has closed, re-run `aws s3 sync s3://<existing bucket> s3://<new bucket>` — responses submitted through the old address's links after the first sync exist only in the existing bucket, not the new one",
        "Delete the 3 existing stacks (Hosting → Auth → Drill, in that order)",
      ],
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Do not rush step 9.** Survey links already handed out point at the old address, so deleting the
old stacks kills them. Wait until response collection for any in-flight survey has finished, re-sync in
step 8, and only then delete.

The existing Drill stack was created with \`removalPolicy: DESTROY\` and \`autoDeleteObjects: true\`,
and it has no versioning — deleting it removes every object in that bucket immediately, with no way to
recover them. Skip the re-sync in step 8, and any response that arrived through the old links after the
first sync is gone for good the moment you delete.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**What gets deployed is the latest commit on \`main\` — anything unpushed is not deployed.** The
instance clones the repository at boot and moves onto \`origin/main\` as it is at that moment. Run
\`git push\` before deploying.

One consequence follows: **\`cdk deploy\` does not update code.** Pushing a commit does not replace
the instance, so use [updating the code](/manual#redeploy) instead.`,
    },
    {
      kind: "details",
      summary: "Seed accounts and their temporary password",
      md: `Deploying creates one administrator and one PM account. Both start with the
\`SeedPassword\` value you passed to the deploy command, and it is a **temporary** password —
each user sets their own at first login, and a redeploy does not reset it.

\`\`\`
npx cdk deploy --all --require-approval never \\
  --parameters AipdsAuthStack:SeedPassword='<temporary-password>'
\`\`\`

The parameter is required and has no default. CloudFormation rejects a value that does not
satisfy the pool policy (8+ characters with an uppercase letter, a lowercase letter, a digit,
a symbol, and no spaces) before the deployment starts. It is \`NoEcho\`, so the value never
lands in the template or stack events.

The temporary password is valid for 30 days. If more time passes between deployment and the
workshop and it expires, issue a fresh one with **Reset password** in [user
management](/manual#invite).`,
    },
    { kind: "heading", id: "region", text: "Changing the region" },
    {
      kind: "md",
      md: `Seoul (\`ap-northeast-2\`) is the default. An environment variable changes it; no code edits.`,
    },
    {
      kind: "cmd",
      lines: ["CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all --require-approval never"],
    },
    { kind: "heading", id: "redeploy", text: "Updating the code" },
    {
      kind: "md",
      md: `**Not with \`cdk deploy\`.** No commit is pinned in the deployment, so pushing one does not
replace the instance and \`cdk deploy\` ends with "no changes". \`aipds-update\` on the instance
does the update — **there is no instance replacement, so it is usable mid-workshop.**`,
    },
    {
      kind: "cmd",
      caption: "Push first, then run one line over SSM",
      lines: [
        "git push",
        "aws ssm start-session --target <InstanceId>",
        "sudo aipds-update",
      ],
    },
    {
      kind: "md",
      md: `It moves the tree onto \`origin/main\` and acts on **only what changed**.

| What changed | What it does | Disruption |
|---|---|---|
| Rules (the submodule) or config only | updates the tree | none (the next turn reads the new rules) |
| Test files only (\`backend/tests/\`, \`*.test.ts(x)\` and the like) | updates the tree | none — running code never reads them, so nothing restarts or rebuilds |
| Backend | restarts the backend | conversations and build sessions in progress are cut. Prototypes that were hosted come back one at a time (a few minutes each) |
| Frontend | rebuilds and restarts | users already connected may hit errors for 1–2 min |
| Nothing (already current) | nothing at all | none |

- Restarting the backend **cuts off conversations and build sessions in progress.** A cut-off
  conversation shows "The server restarted and this task was interrupted" when the workspace is
  reopened, and asking again continues it. A running build session goes down the resume path instead. Apply frontend
  and backend updates during a break.
- Check what is running with \`git -C /opt/aipds rev-parse HEAD\`. To check the app answers, hit it
  directly with \`curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:3000/\` — nginx returns 403
  to requests without CloudFront's secret header, so go around it.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Do not edit files directly on the instance.** \`aipds-update\` moves the tree onto
\`main\` and reverts those edits. Push your fix, then update.`,
    },
    { kind: "heading", id: "ruleset", text: "Where the AI-PLC ruleset lives" },
    {
      kind: "md",
      md: `The methodology that drives the conversation — the questions, the scoring frameworks, the
output formats — is not code in this repository. It is the **upstream AI-PLC ruleset**, carried as a
\`steering-files/\` **git submodule** pinned to an upstream commit and used unmodified, rather than
copied in: a copy drifts, and the canonical source is upstream. The backend copies the ruleset into
the agent's working folder on **every turn**, so a change to the rules takes effect on the next turn.

That has one consequence: **you have to clone the submodule too.** Clone without
\`--recurse-submodules\` and \`steering-files/\` stays an empty directory, which means the agent runs
with no ruleset — and **that raises no error.** The only symptom is a conversation that does not
follow the methodology.`,
    },
    {
      kind: "cmd",
      caption: "When cloning, or to fill it in after the fact",
      lines: [
        "git clone --recurse-submodules <REPO_URL>",
        "git submodule update --init --recursive",
      ],
    },
    {
      kind: "md",
      md: `To pick up new upstream rules, move the submodule pointer, push that commit, then run
\`sudo aipds-update\`. That commit is what records which ruleset a deployment runs. **If the workflow
itself needs to change, the change belongs upstream, not in this repository.**`,
    },
    {
      kind: "cmd",
      lines: [
        "git submodule update --remote steering-files",
        "git add steering-files && git commit -m \"chore: move the ruleset pointer\" && git push",
      ],
    },
    { kind: "heading", id: "sandbox", text: "The sandbox and the IMDS block" },
    {
      kind: "md",
      md: `The Discovery and build agents and hosted prototypes run **as different users from the backend**
(\`aipds-agent\`, \`aipds-proto\`), inside a systemd sandbox that only sees their own project's folder.
Instance metadata (IMDS) is blocked, so they cannot obtain the instance role; they get short-lived
credentials for a **role that can only invoke Bedrock** (\`AipdsAgentCredsStack\`) instead. This is the
boundary that keeps uploaded documents and AI-written code away from the app's folder, other projects,
and the backend's secrets.

**The instance turns this on by itself.** The two separate stacks write their values to SSM Parameter
Store, and \`aipds-harden sync\` on the instance applies them once at boot and every 2 minutes after —
restarting the backend only when a value changed, and leaving the current settings alone when it cannot
read a value.

| Command (\`sudo /opt/aipds/infra/scripts/aipds-harden …\`) | What it does |
|---|---|
| \`status\` | Current state — users, the launcher, the IMDS block, the sync timer, running sandboxes |
| \`sync\` | Apply the stack values now (what the timer does every 2 minutes) |
| \`disable\` | Run agents and prototypes directly as the backend user (the rollback). **Holds the sync** so the timer does not turn it back on |
| \`imds allow\` | Let the sandbox reach IMDS again. Also holds the sync |
| \`enable <AgentRoleArn>\` / \`imds block\` | Turn it back on by hand; releases the hold |

These commands restart the backend — conversations and builds in progress are cut, so use them during
a break.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**When the sandbox is on but unusable, it does not fall back to running directly.** That would
quietly hand the agents and prototypes the whole instance role. Instead conversation turns fail, and
starting a prototype is refused with *Hosting did not start because the prototype sandbox is unavailable…*. Fix the cause
shown in the backend log, or run \`aipds-harden disable\` to run directly on purpose.`,
    },
    { kind: "heading", id: "hotfix", text: "Getting a fresh instance" },
    {
      kind: "md",
      md: `Only needed when you change infrastructure. \`cdk deploy\` replaces the instance, and the new one
picks up the latest \`main\` as it boots. It takes 5–10 minutes to boot and finish building, with 502s
in the meantime — for code-only changes, use the update above.

Prototype source, share links and hosting state live in S3, so they come back on the new instance —
cards still read built and links already handed out still open. Prototypes that were hosted come
back one at a time after boot (a few minutes each); until then their links answer "prototype not running".`,
    },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk deploy AipdsHostingStack --require-approval never"],
    },
    {
      kind: "md",
      md: `The flip side: **once an environment is in use, keep the instance from being replaced by
accident.** The AMI is not pinned and user-data is part of the template, so a HostingStack deploy made
for an unrelated reason can still replace the instance. With a stack policy that refuses replacement
and deletion, plus termination protection, a deploy that would replace it fails and rolls back instead
— the command above is refused too while the policy is in place. Lift the policy only when you really
mean to replace it.`,
    },
    {
      kind: "cmd",
      caption: "Refuse HostingStack replacement and deletion, and turn on termination protection",
      lines: [
        "aws cloudformation set-stack-policy --stack-name AipdsHostingStack --stack-policy-body \\",
        "  '{\"Statement\":[{\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":[\"Update:Replace\",\"Update:Delete\"],\"Resource\":\"*\"},",
        "                 {\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"Update:Modify\",\"Resource\":\"*\"}]}'",
        "aws cloudformation update-termination-protection --stack-name AipdsHostingStack \\",
        "  --enable-termination-protection",
      ],
    },
    {
      kind: "md",
      md: `To update just the three instance-side stacks (\`AipdsAgentCredsStack\`, \`AipdsPreviewStack\`,
\`AipdsUploadCorsStack\`) in such an environment, pin their values with environment variables and
deploy them without touching HostingStack. Give all four values or none.`,
    },
    {
      kind: "cmd",
      lines: [
        "cd infra",
        "AIPDS_INSTANCE_ROLE_ARN=<InstanceRole ARN> \\",
        "AIPDS_PREVIEW_ORIGIN_DNS=ec2-<a-b-c-d>.<region>.compute.amazonaws.com \\",
        "AIPDS_ORIGIN_VERIFY_SECRET_ARN=<OriginVerifyHeader secret ARN> \\",
        "AIPDS_ARTIFACTS_BUCKET=<artifacts bucket name> \\",
        "  npx cdk deploy --exclusively AipdsAgentCredsStack AipdsPreviewStack AipdsUploadCorsStack \\",
        "  --require-approval never",
      ],
    },
    { kind: "heading", id: "teardown", text: "Tearing it down" },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk destroy --all"],
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**The user pool goes with it, so every user account disappears.** Download anything in S3 you
want to keep first. And a deployed stack **keeps costing money** (EC2 running continuously, storage,
plus a Bedrock call per conversation turn) — take it down when it is not in use.

If HostingStack has [termination protection](/manual#hotfix) on, the deletion is refused. Turn it off
first with
\`aws cloudformation update-termination-protection --stack-name AipdsHostingStack --no-enable-termination-protection\`.`,
    },
    { kind: "heading", id: "troubleshooting", text: "Troubleshooting" },
    {
      kind: "md",
      md: `| Symptom | Cause and what to do |
|---|---|
| CloudFront 502 right after deploying | The first EC2 build is still running (5–10 min). Wait. To watch it, open an SSM session and run \`sudo tail -f /var/log/cloud-init-output.log\` |
| Permission error on the first conversation (\`AccessDeniedException\`) | For a model the account has never called, this happens during the few minutes its subscription is being created — send it again shortly after. If it persists, the **Anthropic first-time-use form** has not been submitted or the account has no Marketplace payment method ([deploying](/manual#deploy)) |
| Every conversation fails, and starting hosting is refused with *the prototype sandbox is unavailable* | The sandbox is on but its startup check failed. Look at \`aipds-harden status\` and the backend log ([sandbox](/manual#sandbox)) |
| Redirect error after signing in | Callback URL registration failed. Re-run \`cdk deploy AipdsHostingStack\` |
| Stack refuses to redeploy, stuck in \`ROLLBACK_COMPLETE\` | A stack whose first creation failed cannot be updated. Destroy that stack, then deploy again. \`UPDATE_ROLLBACK_COMPLETE\` (a failed update of an existing stack) just needs a redeploy |
| \`cdk synth\` asks for credentials | HostingStack looks up the deployment region's CloudFront prefix list. The result is cached in the local \`cdk.context.json\`, so it is only needed once per clone |
| Prototype preview returns 404 | That is the intended response — enter through the [share link](/manual#share) |
| English interface but Korean documents | Correct — [document language](/manual#doc-language) is separate from screen language |
| Long messages drop the connection | Too much in a single message. Split it, or [attach it as a file](/manual#attach) |
| The screen is frozen after sleep or a screensaver | Only the **live view** was lost — the AI kept working on the server and the documents were saved. The screen reattaches by itself and picks up from what it missed. If "The connection dropped" appears, refresh — a task still in progress refills from the start, and a finished one comes back as chat history |
| I refreshed during a prototype build | The build keeps going on the server. Press **Open session** on the card — the session's conversation (questions and answers included) comes back from the start and the panel reattaches to the build in progress |
| I refreshed while the AI was working | That is fine. The workspace reattaches to the task in progress as it opens, and the input stays locked until the task finishes. Opening the same project in another tab shows the same task |
| Chat history looks empty | The instance may have been replaced. If a refresh does not bring it back, check the backend log |
| One feature fails and the screen gives no reason | Usually IAM. \`AccessDenied\` in the backend log names the action |
| SSH does not connect | By design. There is no SSH port; only SSM is open |

**When a symptom leaves no reason on screen, read the backend log first.**`,
    },
    {
      kind: "cmd",
      caption: "The backend log — often the only place the cause is recorded",
      lines: [
        "aws ssm start-session --target <InstanceId>",
        "sudo journalctl -u aipds-backend -f",
        "sudo journalctl -u aipds-backend --since -1h | grep -v '/proto/'",
      ],
    },
    { kind: "heading", id: "local-dev", text: "Running it locally" },
    {
      kind: "md",
      md: `Frontend (:3000) → backend (:8000) → the agent inside the backend calls Bedrock. You still need
the S3 bucket and the role, so deploying just \`AipdsDrillStack\` is enough. Python **3.11** and
Node.js 20+ are required.`,
    },
    {
      kind: "cmd",
      caption: "Install once, then run in two terminals",
      lines: [
        "git submodule update --init --recursive",
        "cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e \".[dev]\"",
        "cd ../frontend && npm install",
        "cp ../backend/.env.example ../backend/.env",
        "",
        "cd backend && .venv/bin/python -m uvicorn aipds.app:app --port 8000 --reload",
        "cd frontend && npm run dev",
      ],
    },
    {
      kind: "md",
      md: `The full environment-variable list is in the systemd units in \`infra/lib/user-data.ts\`, each
line commented, and why the stacks are shaped the way they are is in the repository's \`infra/README.md\`.
**The reasoning behind the design decisions lives in the commit messages and code comments** —
"why is it like this" is a \`git log\` question.`,
    },
  ],
};
