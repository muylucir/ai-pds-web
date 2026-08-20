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
VPC), and **Bedrock model access enabled in the deployment region** for the Claude model you intend
to use.

Skip that last one and the deployment succeeds while the first conversation fails — it is the most
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
      md: `The three stacks reference one another, so deploy them **together with \`--all\`**.

| Stack | What it creates |
|---|---|
| \`AipdsDrillStack\` | The artifacts S3 bucket + the backend execution role |
| \`AipdsAuthStack\` | Cognito user pool + hosted sign-in + role groups + seed accounts |
| \`AipdsHostingStack\` | VPC + EC2 + CloudFront |

It takes **15–20 minutes**. Even after \`cdk deploy\` returns, EC2 may still be building the backend
and frontend, so **a few minutes of 502 responses is normal.**

The address to open is the \`AipdsHostingStack.DistributionDomain\` output.`,
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
until the old stacks are deleted in step 8 below. Leave it frozen until then.`,
    },
    {
      kind: "steps",
      items: [
        "`cdk deploy AipdsDrillStack AipdsAuthStack` — the new bucket and user pool",
        "`aws s3 sync s3://<existing bucket> s3://<new bucket>` — moves the artifacts over. The key prefixes carry no product name, so the layout comes up unchanged. After it finishes, compare object counts: run `aws s3api list-objects-v2 --bucket <new bucket> --query 'length(Contents)'` and the same command against the existing bucket — the deletion below is irreversible, so seeing project cards on screen is not enough to catch a partially failed sync",
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
      md: `**Do not rush step 8.** Survey links already handed out point at the old address, so deleting the
old stacks kills them. Wait until response collection for any in-flight survey has finished, re-sync in
step 7, and only then delete.

The existing Drill stack was created with \`removalPolicy: DESTROY\` and \`autoDeleteObjects: true\`,
and it has no versioning — deleting it removes every object in that bucket immediately, with no way to
recover them. Skip the re-sync in step 7, and any response that arrived through the old links after the
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
      summary: "Seed accounts and replacing their password",
      md: `Deploying creates one administrator and one PM account. Their password is a constant in the CDK
source, so it **appears in plaintext in the CloudFormation template and stack events, and a redeploy
resets it to that value.**

That is fine for a demo or an evaluation. For anything real, replace \`SEED_PASSWORD\` in
\`infra/lib/auth-client-config.ts\` and use [accounts invited through user
management](/manual#invite) instead of the seed accounts.`,
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
| Rules or config only | updates the tree | none (the next turn reads the new rules) |
| Backend | restarts the backend | conversations and build sessions in progress are cut |
| Frontend | rebuilds and restarts | users already connected may hit errors for 1–2 min |
| Nothing (already current) | nothing at all | none |

- Restarting the backend **cuts off conversations and build sessions in progress.** Conversations
  resume when reopened; a running build session goes down the resume path instead. Apply frontend
  and backend updates during a break.
- Check what is running with \`git -C /opt/aipds rev-parse HEAD\`.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Do not edit files directly on the instance.** \`aipds-update\` moves the tree onto
\`main\` and reverts those edits. Push your fix, then update.`,
    },
    { kind: "heading", id: "hotfix", text: "Getting a fresh instance" },
    {
      kind: "md",
      md: `Only needed when you change infrastructure. \`cdk deploy\` replaces the instance, and the new one
picks up the latest \`main\` as it boots. It takes 5–10 minutes to boot and finish building, with 502s
in the meantime — for code-only changes, use the update above.`,
    },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk deploy AipdsHostingStack --require-approval never"],
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
plus a Bedrock call per conversation turn) — take it down when it is not in use.`,
    },
    { kind: "heading", id: "troubleshooting", text: "Troubleshooting" },
    {
      kind: "md",
      md: `| Symptom | Cause and what to do |
|---|---|
| CloudFront 502 right after deploying | The first EC2 build is still running (5–10 min). Wait |
| Permission error on the first conversation | **Bedrock model access** for that model is off in the deployment region |
| Redirect error after signing in | Callback URL registration failed. Re-run \`cdk deploy AipdsHostingStack\` |
| Stack refuses to redeploy, stuck in \`ROLLBACK_COMPLETE\` | A stack whose first creation failed cannot be updated. Destroy that stack, then deploy again |
| Prototype preview returns 404 | That is the intended response — enter through the [share link](/manual#share) |
| English interface but Korean documents | Correct — [document language](/manual#doc-language) is separate from screen language |
| Long messages drop the connection | Too much in a single message. Split it, or [attach it as a file](/manual#attach) |
| The screen is frozen after sleep or a screensaver | Only the **live view** was lost — the AI kept working and the documents were saved. It reattaches by itself, so waiting picks the stream back up; if the turn finished meanwhile, a refresh brings the content back |
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
line commented, and the rest of the deployment procedure is in the repository's \`README.md\`.
**The reasoning behind the design decisions lives in the commit messages and code comments** —
"why is it like this" is a \`git log\` question.`,
    },
  ],
};
