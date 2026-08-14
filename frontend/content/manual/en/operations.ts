import type { ManualSection } from "../types";

export const operations: ManualSection = {
  id: "operations",
  title: "Install, operate, troubleshoot",
  lede: "For whoever stands Pathfinder up and keeps it running. If you only use it, you can skip this.",
  blocks: [
    {
      kind: "callout",
      tone: "note",
      md: `This section is for **whoever deploys Pathfinder into an AWS account**. If someone handed you a
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
| \`PathfinderDrillStack\` | The artifacts S3 bucket + the backend execution role |
| \`PathfinderAuthStack\` | Cognito user pool + hosted sign-in + role groups + seed accounts |
| \`PathfinderHostingStack\` | VPC + EC2 + CloudFront |

It takes **15–20 minutes**. Even after \`cdk deploy\` returns, EC2 may still be building the backend
and frontend, so **a few minutes of 502 responses is normal.**

The address to open is the \`PathfinderHostingStack.DistributionDomain\` output.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**What gets deployed is a commit — anything unpushed is not deployed.** The instance clones the
repository at boot and pins one commit. Because the clone happens at boot, deploying an unpushed
commit means \`cdk deploy\` succeeds and **only the instance fails to start** (you see a 502).
Run \`git push\` before deploying.`,
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
    { kind: "heading", id: "redeploy", text: "Redeploying code only" },
    {
      kind: "cmd",
      lines: [
        "git push",
        "cd infra && npx cdk deploy PathfinderHostingStack --require-approval never",
      ],
    },
    {
      kind: "md",
      md: `When the deploy commit changes, the EC2 instance is **replaced** and rebuilds from the new
commit. The new instance takes 5–10 minutes to boot and finish building, and you get 502s in the
meantime.

To deploy or roll back to a specific commit, name it:
\`CDK_DEPLOY_REF=<sha> npx cdk deploy PathfinderHostingStack\``,
    },
    { kind: "heading", id: "hotfix", text: "Fixing without replacing the instance" },
    {
      kind: "md",
      md: `Use this when you cannot afford the 5–10 minute gap, such as mid-workshop.
\`/opt/pathfinder\` is a **git working tree**, so it can be updated in place.`,
    },
    {
      kind: "cmd",
      caption: "Push first, then move the deployed commit over SSM",
      lines: [
        "aws ssm start-session --target <InstanceId>",
        "sudo -u pathfinder git -C /opt/pathfinder fetch origin",
        "sudo -u pathfinder git -C /opt/pathfinder checkout --detach <sha>",
      ],
    },
    {
      kind: "cmd",
      caption: "Rebuild if you changed the frontend; a restart is enough for the backend alone",
      lines: [
        "cd /opt/pathfinder/frontend",
        "sudo -u pathfinder env NEXT_PUBLIC_API_BASE_URL=/api HOME=/opt/pathfinder npm run build",
        "sudo systemctl restart pathfinder-frontend",
        "",
        "sudo systemctl restart pathfinder-backend",
      ],
    },
    {
      kind: "md",
      md: `- **Do not drop \`NEXT_PUBLIC_API_BASE_URL=/api\`.** It is inlined into the client bundle, so
  building without it makes the browser call \`localhost:8000\` and every API call dies — the screens
  render and nothing works.
- During the 1–2 minutes the build runs, users already connected may hit errors. Do it during a break.
- Restarting the backend **cuts off conversations and build sessions in progress.** Conversations
  resume when reopened; a running build session goes down the resume path instead.
- Check what is running with \`git -C /opt/pathfinder rev-parse HEAD\`.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**A hotfix is overwritten by the next \`cdk deploy\`** — the instance is rebuilt from the deploy
commit at that point. Always push what you fixed, and deploy that commit or later next time.`,
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
| Redirect error after signing in | Callback URL registration failed. Re-run \`cdk deploy PathfinderHostingStack\` |
| Stack refuses to redeploy, stuck in \`ROLLBACK_COMPLETE\` | A stack whose first creation failed cannot be updated. Destroy that stack, then deploy again |
| Prototype preview returns 404 | That is the intended response — enter through the [share link](/manual#share) |
| English interface but Korean documents | Correct — [document language](/manual#doc-language) is separate from screen language |
| Long messages drop the connection | Too much in a single message. Split it, or [attach it as a file](/manual#attach) |
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
        "sudo journalctl -u pathfinder-backend -f",
      ],
    },
    { kind: "heading", id: "local-dev", text: "Running it locally" },
    {
      kind: "md",
      md: `Frontend (:3000) → backend (:8000) → the agent inside the backend calls Bedrock. You still need
the S3 bucket and the role, so deploying just \`PathfinderDrillStack\` is enough. Python **3.11** and
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
        "cd backend && .venv/bin/python -m uvicorn pathfinder.app:app --port 8000 --reload",
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
