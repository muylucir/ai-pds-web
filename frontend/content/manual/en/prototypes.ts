import type { ManualSection } from "../types";

export const prototypes: ManualSection = {
  id: "prototypes",
  title: "Building prototypes",
  lede: "Turn a spec into an app that actually runs, then share a link so people can try it.",
  blocks: [
    {
      kind: "md",
      md: `Once Discovery has written a prototype spec, it shows up as a card in the
Prototypes tab. One card is one prototype, and the status badge tells you how far it has got.

The colours and typeface come from whatever the administrator uploaded under
[Brand design](/manual#brand-design). With nothing uploaded, prototypes use the default look.`,
    },
    {
      kind: "mockup",
      id: "prototype-card",
      caption: "A prototype card, and the completion card that appears when the build finishes",
    },
    {
      kind: "md",
      md: `| Status | Meaning | What to do next |
|---|---|---|
| Not built | Nothing has been built yet | **Start build** |
| Building | It is being built right now | **Open session** to watch |
| Built | It exists but is not running | **Start hosting** |
| Running | The preview is live | **Open preview** · **Copy link** |
| Failed | The build did not finish | Check **Logs**, then **Rebuild** |`,
    },
    {
      kind: "steps",
      items: [
        "**Start build** — a build session opens and the AI reads the spec and starts working.",
        "It asks questions when it needs to. You answer the same way as in the workspace.",
        "When it is done the session closes itself and a **Build complete** card appears.",
        "**Start hosting** actually runs the app — the badge moves through *Installing dependencies* → *Building* → *Starting the server*. It can take a few minutes.",
        "**Open preview** to check it, **Copy link** to send it to someone.",
      ],
    },
    { kind: "heading", id: "session", text: "The build session" },
    {
      kind: "md",
      md: `A build is a conversation. The session view shows progress and changed files, and **■** stops
it exactly as in the workspace. You can direct it mid-build — "do the sign-in screen first",
"make this one mobile-first".

A session lasts **one build**. When the AI declares it finished, the session closes itself — that
is a normal ending, not a dropped connection.`,
    },
    { kind: "heading", id: "complete-card", text: "The completion card" },
    {
      kind: "md",
      md: `- **Start hosting** — runs what was built and opens the preview.
- **Continue improving** — opens a fresh session to change things. Only a summary of the previous build is carried over, which keeps it light.
- **Close** — collapses the card. **Open session** brings you back later.

The buttons may be briefly unavailable (*Finishing the build*). They become active a few
seconds later.`,
    },
    { kind: "heading", id: "share", text: "Sharing the preview" },
    {
      kind: "md",
      md: `**Copy link** produces a **share link that opens without an account**. Whoever receives it can
try the prototype without signing in — send it straight to the customers or colleagues whose
reactions you want.

When you are done, **Stop hosting** takes it down. After that the share link stops opening too.`,
    },
    {
      kind: "callout",
      tone: "note",
      md: `Typing a preview path into the address bar yourself **returns 404, and that is correct.**
Access is granted to your browser when you open the share link, so you have to enter through the
copied link.`,
    },
    { kind: "heading", id: "limits", text: "How many can build at once" },
    {
      kind: "md",
      md: `A server allows a limited number of builds at the same time. When they are all taken you see
*Another team is building a prototype* or *The concurrent-build limit has been reached*. A slot
frees up when a running build finishes, so pressing the button again shortly works.`,
    },
    { kind: "heading", id: "proto-reset", text: "Resetting" },
    {
      kind: "md",
      md: `**Reset** on a card returns that prototype to **Not built**. The spec stays.

Deleted — the build output and any running server · the build conversation · the validation
survey and its responses.
Kept — the design document (\`PROTOTYPE-*.md\`), which is why you can build again.

Use it when the direction changed enough that starting over is cleaner. For smaller changes,
**Continue improving** is the right button, not Reset.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Survey responses are destroyed by a reset and cannot be recovered.** Export the CSV before
resetting a prototype that has collected responses. The confirmation dialog tells you the current
response count.`,
    },
    { kind: "heading", id: "download", text: "Downloading" },
    {
      kind: "md",
      md: `**Download** gives you the built prototype's source as an archive. Use it when a build team
wants that code as a starting point, or when you want to run it somewhere else yourself.`,
    },
  ],
};
