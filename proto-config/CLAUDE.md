# Prototype build contract (shared config — mandatory)

<!--
WHY THIS FILE IS IN ENGLISH, and why that is not a language directive.

This file lives in the build agent's shared CLAUDE_CONFIG_DIR ("user" level),
so EVERY project reads it regardless of the language its user chose — it cannot
carry a per-project language. discovery-config/CLAUDE.md, which sits in the
same structural position for the Discovery agent, learned this the hard way on
2026-08-04: an English project's chat ran in Korean because that whole file was
Korean prose. **The language a document is written in is itself a language
signal**, even when the document never says which language to use.

So the rule here is the same: this file must be language-NEUTRAL, and for a
document the model reads that means English. The per-project language reaches
the build agent through proto/prompts.py (two complete language versions) and
the workspace CLAUDE.md — those are the levels that can vary per project.

Keep this file in English when editing it. backend/tests/test_agent_language.py
pins the invariant for both shared config dirs.
-->

Write non-ASCII text (Korean, etc.) in tool-call parameters (JSON) as literal
UTF-8 — never as `\uXXXX` escapes. This is an encoding rule, not a language
rule: it says nothing about WHICH language to write in, only that whatever
language you write must reach the tool as real characters.

Use the **shadcn-design** skill for the visual design of every prototype.

## Processes and ports — do not kill Pathfinder itself (highest priority)

**You are running inside the very server that runs Pathfinder.** The backend and
the frontend run as **the same user you do (`pathfinder`)**, so any signal you
send reaches those processes with nothing in the way. This has already caused an
incident (2026-08-01): a build agent started Playwright chromium for browser
verification, and in doing so the process on port 3000 was SIGKILLed — that was
the Pathfinder frontend, and workshop participants saw "the connection dropped"
on their screens.

What follows is not a preference. It is **forbidden**.

- **Never touch ports 3000 and 8000.** 3000 is the Pathfinder frontend, 8000 the
  backend. Do not bind them, do not probe them, and do not try to clean up
  whatever holds them. The port a prototype gets is **chosen by hosting from the
  4000–8000 range** (`_scan_port` in `host.py`) — that is not your decision.
- **Never kill a process you did not start.** No `pkill`, `killall`,
  `kill -9 $(lsof -ti:...)`, `fuser -k` and friends. Clean up only the processes
  **you started yourself**.
- **Never run browser automation (Playwright, Puppeteer, chrome-headless).** The
  user checks the screen through the live preview in the prototypes tab. Do not
  start a browser to take screenshots. That includes `npx playwright ...` and
  `npm run test:e2e` — the repo's `playwright.config.ts` **targets port 3000**,
  so running it reproduces the incident above exactly.
- **Never end a turn with a dev or production server still running.** Verify the
  build with `npm run build`. If you genuinely need a runtime check, follow the
  discipline below.

### If you really must start a server

`npm run start`/`dev` **starts the real server as a child of npm.** So killing
the job spec (`kill %1`) or npm's PID alone **orphans the actual listener, which
goes on holding the port** — our own hosting code signals the process group for
the same reason (`stop()` in `host.py`). Always do it this way:

```bash
# 1) A port outside 4000-8000, in its own process group
setsid npm run start > /tmp/smoke.log 2>&1 &
PGID=$!
sleep 6
curl -s http://localhost:9123/... || true      # verify

# 2) Clean up the whole group. Drop this line and the server survives
kill -- -"$PGID" 2>/dev/null || true
wait "$PGID" 2>/dev/null || true
```

Do not copy shapes like `kill %1 2>/dev/null`. That pattern really did leave
servers behind, and it even produced `kill %12>/dev/null` (parsed as
`kill %12`, which kills nothing). When you add a redirect, **keep a space
between `%1` and `2>`**.

## Bedrock calls — do not send sampling parameters

When prototype code calls Bedrock Claude, **do not send `temperature`, `top_p`
or `top_k`.** Claude Opus 4.7 and later models (Opus 4.7, 4.8, 5, Sonnet 5)
removed these parameters, and sending them fails the whole request — this is the
error as measured in this deployment's `ap-northeast-2`:

```
ValidationException: The model returned the following errors:
  `temperature` is deprecated for this model.
```

Put **only `maxTokens`** in the Converse API's `inferenceConfig`:

```js
const inferenceConfig = { maxTokens };   // no temperature/topP
```

**Do not branch on the model — just never send them.** Do not write a workaround
that regex-matches the model ID to exclude particular models: the default model
changes through an environment variable, and each time the regex misses the new
model the same error returns (this happened once — a pattern matching only
`opus-(4-8|5)` missed `sonnet-5`). If you need determinism or variety in the
output, ask for it in the prompt.

For the same reason, **do not send `budget_tokens` (extended thinking)** — it was
removed in Opus 4.7 as well. When you need reasoning depth, use
`thinking: {type: "adaptive"}` under `additionalModelRequestFields`.

Exception: Sonnet 4.6 and earlier still accept `temperature`. Follow the rule
anyway — the goal is code that works on every model.

## Use Next.js version 15

## Prototypes are served from a sub-path — `basePath` is required

A prototype is served through a reverse proxy under
`/proto/{project_id}/{slug}/`, not at the root. Hosting passes that prefix at
build time through the `NEXT_PUBLIC_BASE_PATH` environment variable (the
framework-neutral alias `PROTO_BASE_PATH` carries the same value).

**A Next.js prototype MUST include the following in `next.config.js` (or
`.ts`/`.mjs`):**

```js
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  basePath,
  // Keep asset URLs prefixed. basePath alone already covers _next/ assets,
  // but stating it makes the intent visible.
  assetPrefix: basePath || undefined,
  // Normalize in the same direction as the proxy. See the "trailingSlash"
  // section below — omit it and you get an infinite redirect loop
  // (ERR_TOO_MANY_REDIRECTS).
  trailingSlash: true,
};

export default nextConfig;
```

Leave `basePath` out and **the build bakes root-relative asset URLs** — Next.js
inlines this value at build time, not at runtime, so it cannot be fixed after
deployment and the screen breaks with `/_next/static/...` 404s. In an
environment with no prefix (running locally on its own) the variable is absent,
the value is `""`, and everything still works.

**Never hardcode it.** Do not write the prefix into `next.config.js` as a
string; read it from the environment as above — the value changes when the slug
does.

**When you write paths yourself:** `<Link href="/about">` and
`router.push("/about")` get `basePath` prepended by Next.js, so leave them
alone. References that bypass the framework — `fetch("/api/...")`,
`<img src="/logo.png">` — are not handled for you, so compose them as
`` `${basePath}/...` `` or use Next.js's `<Image>`.

**If it is not Next.js** (Vite, CRA, …): take the same value from
`PROTO_BASE_PATH`. The corresponding settings are `base` for Vite and
`PUBLIC_URL` for CRA.

## `trailingSlash: true` — omit it and you get an infinite redirect loop

This is **as mandatory as `basePath`**. Miss it and the screen never opens at
all:

```
This page isn't working
... redirected you too many times.
ERR_TOO_MANY_REDIRECTS
```

**The cause is two normalizations pointing in opposite directions.** The proxy
and the prototype each try to restore the same URL to their own "correct" form,
undoing each other's result:

| Party | Rule |
|---|---|
| Pathfinder proxy | slash **absent → present** (`/proto/{pid}/{slug}` → `/proto/{pid}/{slug}/`) |
| Next.js default (`trailingSlash: false`) | slash **present → absent** |

The measured cycle (reproduced against the proxy code):

```
browser   /api/proto/p1/demo/
  → prototype 308 → /api/proto/p1/demo      (Next strips the slash)
browser   /api/proto/p1/demo
  → proxy 307     → /api/proto/p1/demo/     (proxy adds the slash)
  → repeat forever
```

**The proxy side cannot change.** It adds the slash because of relative asset
references: without the slash, at `.../{slug}` the browser resolves
`href="styles.css"` against `.../{pid}/` (the slug is gone) and every asset
502s. With the slash, the document's base becomes `.../{slug}/` and relative
references land inside the prototype.

**So the prototype is the side that adapts.** With `trailingSlash: true`, Next
normalizes in the same direction as the proxy (the slash-present form) and no
cycle can form.

Like `basePath`, this setting is baked in at build time, so a prototype that
missed it **is only fixed by rebuilding** — it cannot be patched after
deployment.

**If it is not Next.js:** find the equivalent setting and align it to the
slash-present form. Static servers (`serve`, `http-server`, …) usually treat
directory URLs as-is and need no configuration, but if an SPA router normalizes
URLs itself, configure it **not to strip** the trailing slash.
