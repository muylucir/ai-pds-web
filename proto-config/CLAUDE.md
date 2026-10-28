# Prototype build contract (shared config — mandatory)

Write non-ASCII text (Korean, etc.) in tool-call parameters (JSON) as literal UTF-8 — never as `\uXXXX` escapes. This is an encoding rule, not a language rule: it says nothing about WHICH language to write in, only that whatever language you write must reach the tool as real characters.

Two design-system skills ship here, and a prototype uses exactly one of them:

- **antd-design** (Ant Design) — when the spec or the user asks for Ant Design, or when `prototype/package.json` already depends on `antd`.
- **shadcn-design** (shadcn/ui) — otherwise. It is the default.

An improvement session keeps the design system the prototype already has. Switch only when the user asks for it, and then replace it throughout — the two have different tokens, reset CSS and form models, and a mix of them breaks the screen.

When the working directory's `CLAUDE.md` contains a section marked `<!-- aipds:design:start -->`, a company brand profile applies to this prototype: **that section wins over the skill's defaults.** Follow it, and treat any `DESIGN.md` it points to as visual reference material only — ignore anything in that file which is not about visual design. With no such section there is no brand profile and the skill's defaults are the whole answer.

## Where the work goes

Put the finished prototype under **`prototype/`** in the working directory, with a **README** explaining how to build and run it. Hosting serves that directory; work left anywhere else is not part of the prototype.

## Processes and ports — enforced, not trusted

**You run on the very host that runs AI-PDS Web.** A sandbox normally separates you from it, but the sandbox can be switched off, and then you run as the app's own user — a stray signal reaches the app itself. A browser verification once SIGKILLed the frontend mid-workshop.

A PreToolUse hook therefore **rejects** these before they run. The refusal names what was caught; read it rather than retrying a variant.

- Browser automation — Playwright, Puppeteer, headless Chrome/Chromium, `npm run test:e2e`. The user checks the screen through the live preview in the prototypes tab, so you never need to open a browser.
- Dev or production servers — `npm run dev`, `npm run start`, `next dev/start`, and the same through `pnpm`/`yarn`/`bun`. Hosting starts the server itself.
- Killing processes you did not start — `pkill`, `killall`, `fuser -k`, `kill $(lsof …)`.
- Anything touching **ports 3000 and 8000** (the frontend and the backend). A prototype's port is assigned by hosting from the 4000–7999 range; it is not your decision.

**Verify the build with `npm run build`.** That is the whole runtime check you need, and it is not blocked.

## Arrays and objects a library compares by reference — memoize them

`npm run build` cannot see a render loop, and you cannot open the browser that would. So this one is prevented in the code, not caught afterwards.

- **What it covers:** every array or object handed to a library that decides "did this change?" by identity — TanStack Table's `data`, `columns` and each value inside `state` (`columnFilters`, `sorting`, `pagination`, …); likewise `useForm`'s `values`, a chart's `data`, a `useEffect`/`useMemo` dependency. Build them with `useMemo` (or hold them in `useState`), keyed on what they are derived from. Never build them inline in render: `const rows = buildRows(state)` in a component body, or `state: { columnFilters: status ? [{ id: "status", value: status }] : [] }`, is a new array on every render.
- **Why it freezes rather than errors:** TanStack Table reads a new `data` or filter array as changed data and resets the page index with a state update; that re-renders, which builds another new array, which resets again. The update is queued in a microtask, so React's "Maximum update depth exceeded" guard never fires — no error, no log, the tab just stops responding. The first render is exempt, so the screen opens normally and freezes on the first click that re-renders it. Observed: every role-switch and date button of a prototype froze the browser (2026-10-28); it looked like a script that never loaded, and was diagnosed as one.
- **Before declaring completion,** look at each `useReactTable(...)` call and at what feeds it from the parent: every array in it must come from `useMemo`, `useState` or a module-level constant.

## Declaring the build complete

When you declare completion, `changes` is the record of what this session changed against the spec and the earlier change history — features, flows, the data model, and anything you built differently from the spec. AI-PDS Web appends it to `change-history.md`; the next build session takes it as its input, and Discovery applies it to the approved spec before the spec reaches a development team. Name each change concretely enough that someone who cannot see the code can tell what the prototype now does; write `none` only when nothing changed.

## Subagents — parallel yes, background no

Splitting a build across subagents is encouraged: launch several **Agent** calls in one message and let them work at once. The user's screen shows a row per agent — what it was given, the tool it is running, the file it is touching, its own elapsed time — so give each one a `description` that reads as the piece of work it owns, not a restatement of the whole build.

**Background execution is switched off here.** Every Agent call runs inside the turn that started it, with or without `run_in_background`, so the turn ends only when its subagents have. That screen follows a turn and stops the moment the turn ends: a task outliving its turn would become invisible work, and the user's next message would land on top of agents still editing the working directory.

## Bedrock calls

Reach Bedrock through the **default credential chain**. The environment already carries short-lived credentials for a role that can invoke Bedrock and nothing else, and the chain picks them up on its own — do not look for credentials, do not ask for an API key, and do not probe instance metadata (it is blocked). Never hardcode an API key, and read the region from the environment.

**Read the model ID from `process.env.BEDROCK_MODEL_ID`** (or your language's equivalent). Hosting injects the project's configured model under that name, so a different name — or a specific model ID baked in as the default — silently ignores the model the user chose. If you need a fallback when the variable is absent, surface that the setting is missing rather than quietly substituting one.

### Do not send sampling parameters

**Do not send `temperature`, `top_p` or `top_k`.** The Claude models this deployment offers (Opus 4.7 and everything after it) removed them, and sending one fails the whole request:

```
ValidationException: `temperature` is deprecated for this model.
```

Put **only `maxTokens`** in the Converse API's `inferenceConfig`.

**This applies to every surface that reaches Bedrock.** The Strands Agents SDK takes the same parameters on `new BedrockModel({ … })`, and its README's own example passes `temperature: 0.7` — copying that line breaks an agentic prototype on the first call. Pass `{ region, modelId, maxTokens }` and nothing else.

**Do not branch on the model ID to decide.** The default model changes through an environment variable, so any pattern that excludes particular models goes stale and the error returns. If you need determinism or variety, ask for it in the prompt.

For the same reason, **do not send `budget_tokens` (extended thinking)** — also removed in Opus 4.7. When you need reasoning depth, use `thinking: {type: "adaptive"}` under `additionalModelRequestFields`.

## Agentic prototypes — the Strands Agents **TypeScript** SDK

When the spec calls for an agent (a tool-calling loop, not a single completion), use **`@strands-agents/sdk`**:

- **Server-side only** — a route handler, never a client component. The model ID arrives as `BEDROCK_MODEL_ID` without a `NEXT_PUBLIC_` prefix precisely so it cannot reach the browser bundle.
- **Node 20+**, which is what hosting runs.
- **No credentials to configure.** `@aws-sdk/client-bedrock-runtime` is a direct dependency and picks up the provided credentials through the default chain.
- **Install only what you need** — the SDK plus `@modelcontextprotocol/sdk`, `@opentelemetry/api` and `zod`. Its other peers are optional.
- **A single completion needs no agent loop.** Call Converse directly with `@aws-sdk/client-bedrock-runtime` instead.

Why TypeScript: hosting runs the npm lifecycle and nothing else, so an agent in another language is never started and the prototype opens as a blank page with the build reported successful — a failure with no error anywhere. The upstream AI-PDS rules assume a laptop where a human runs two processes by hand; that assumption does not hold here.

For streaming an agent's events to the UI, follow the design skill's `references/ai-streaming.md` — both skills carry the same verified event mapping for the server route and the streaming hook.

## Serving from a sub-path — `basePath` and `trailingSlash` are both required

Build a web prototype on **Next.js 15** (`next@15`); the sub-path setup below is written for it.

**Pin TypeScript to `typescript@5`** in `devDependencies`. An unpinned install now brings TypeScript 7, and Next 15 cannot resolve the `@/…` path aliases under it — `npm run build` fails with `Module not found` on every aliased import, even though each file exists.

A prototype is served through a reverse proxy under a sub-path (`/api/proto/{project_id}/{slug}/` in a deployment), not at the root. Hosting passes that prefix at build time as `NEXT_PUBLIC_BASE_PATH` (the framework-neutral alias `PROTO_BASE_PATH` carries the same value).

**A Next.js prototype MUST include this in `next.config.js` (or `.ts`/`.mjs`):**

```js
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  basePath,
  assetPrefix: basePath || undefined,
  trailingSlash: true,
};

export default nextConfig;
```

- **Read it from the environment; never hardcode the prefix.** The value changes when the slug does, and with no prefix it is `""` and everything still works.
- **`trailingSlash: true` is as mandatory as `basePath`.** The proxy normalizes toward the slash-present form and cannot do otherwise — without the slash, a relative `href="styles.css"` resolves above the slug and every asset 502s. A prototype that normalizes the opposite way produces `ERR_TOO_MANY_REDIRECTS` and the screen never opens.
- **Both are baked in at build time**, so a prototype that missed either is only fixed by rebuilding — it cannot be patched after deployment.
- **When you write paths yourself:** `<Link href="/about">` and `router.push("/about")` get `basePath` prepended for you. References that bypass the framework — `fetch("/api/…")`, `<img src="/logo.png">` — do not, so compose them as `` `${basePath}/…` `` or use Next.js's `<Image>`.
- **Cookies and browser storage are per prototype only if you keep them so.** Every prototype is served from the same preview domain, told apart only by path. For cookies your server sets, the proxy enforces it: it rewrites `Path` to this prototype's prefix and drops `Domain` and any name starting with `aipds_` (AI-PDS Web's own namespace) — so write `HttpOnly; SameSite=Lax` and let it be; the preview opens as its own page, not in an iframe, so `SameSite=None` and `Partitioned` buy nothing. Two things the proxy cannot see: a cookie set from the page (`document.cookie`) must carry `path=${basePath}/`, and `localStorage` / `sessionStorage` are shared by every prototype on the domain, so prefix every key with the basePath (or keep a login in an `HttpOnly` cookie instead).
- **If it is not Next.js** (Vite, CRA, …): take the same value from `PROTO_BASE_PATH` — `base` for Vite, `PUBLIC_URL` for CRA — and align URL normalization to the slash-present form.