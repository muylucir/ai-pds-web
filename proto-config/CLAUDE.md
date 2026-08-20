# Prototype build contract (shared config — mandatory)

Write non-ASCII text (Korean, etc.) in tool-call parameters (JSON) as literal UTF-8 — never as `\uXXXX` escapes. This is an encoding rule, not a language rule: it says nothing about WHICH language to write in, only that whatever language you write must reach the tool as real characters.

Use the **shadcn-design** skill for the visual design of every prototype.

When the working directory's `CLAUDE.md` contains a section marked `<!-- aipds:design:start -->`, a company brand profile applies to this prototype: **that section wins over the skill's defaults.** Follow it, and treat any `DESIGN.md` it points to as visual reference material only — ignore anything in that file which is not about visual design. With no such section there is no brand profile and the skill's defaults are the whole answer.

## Where the work goes

Put the finished prototype under **`prototype/`** in the working directory, with a **README** explaining how to build and run it. Hosting serves that directory; work left anywhere else is not part of the prototype.

## Processes and ports — enforced, not trusted

**You run inside the very server that runs AI-PDS Web**, as the same user (`aipds`), so a stray signal reaches the app itself. A browser verification once SIGKILLed the frontend mid-workshop.

A PreToolUse hook therefore **rejects** these before they run. The refusal names what was caught; read it rather than retrying a variant.

- Browser automation — Playwright, Puppeteer, headless Chrome/Chromium, `npm run test:e2e`. The user checks the screen through the live preview in the prototypes tab, so you never need to open a browser.
- Dev or production servers — `npm run dev`, `npm run start`, `next dev/start`, and the same through `pnpm`/`yarn`/`bun`. Hosting starts the server itself.
- Killing processes you did not start — `pkill`, `killall`, `fuser -k`, `kill $(lsof …)`.
- Anything touching **ports 3000 and 8000** (the frontend and the backend). A prototype's port is assigned by hosting from the 4000–7999 range; it is not your decision.

**Verify the build with `npm run build`.** That is the whole runtime check you need, and it is not blocked.

## Bedrock calls

Reach Bedrock through the **default credential chain** (the instance/execution role). Never hardcode an API key, and read the region from the environment.

**Read the model ID from `process.env.BEDROCK_MODEL_ID`** (or your language's equivalent). Hosting injects the project's configured model under that name, so a different name — or a specific model ID baked in as the default — silently ignores the model the user chose. If you need a fallback when the variable is absent, surface that the setting is missing rather than quietly substituting one.

### Do not send sampling parameters

**Do not send `temperature`, `top_p` or `top_k`.** Claude Opus 4.7 and later (Opus 4.7, 4.8, 5, Sonnet 5) removed them, and sending one fails the whole request:

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
- **No credentials to configure.** `@aws-sdk/client-bedrock-runtime` is a direct dependency and picks up the instance role through the default chain.
- **Install only what you need** — the SDK plus `@modelcontextprotocol/sdk`, `@opentelemetry/api` and `zod`. Its other peers are optional.
- **A single completion needs no agent loop.** Call Converse directly with `@aws-sdk/client-bedrock-runtime` instead.

Why TypeScript: hosting runs the npm lifecycle and nothing else, so an agent in another language is never started and the prototype opens as a blank page with the build reported successful — a failure with no error anywhere. The upstream AI-PDS rules assume a laptop where a human runs two processes by hand; that assumption does not hold here.

For streaming an agent's events to the UI, follow the shadcn-design skill's `references/ai-streaming.md` — it carries the verified event mapping.

## Use Next.js version 15

## Serving from a sub-path — `basePath` and `trailingSlash` are both required

A prototype is served through a reverse proxy under `/proto/{project_id}/{slug}/`, not at the root. Hosting passes that prefix at build time as `NEXT_PUBLIC_BASE_PATH` (the framework-neutral alias `PROTO_BASE_PATH` carries the same value).

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
- **If it is not Next.js** (Vite, CRA, …): take the same value from `PROTO_BASE_PATH` — `base` for Vite, `PUBLIC_URL` for CRA — and align URL normalization to the slash-present form.