import type { ManualSection } from "../types";

export const admin: ManualSection = {
  id: "admin",
  title: "Administrator screens",
  lede: "Managing users, the model list, and the prototype brand. Visible only to the administrator role.",
  blocks: [
    {
      kind: "md",
      md: `**User management**, **Model management** and **Brand design** are in the user menu (the round
icon) at the right of the header. They do not appear for non-administrators, and typing the address
directly sends you back.`,
    },
    { kind: "heading", id: "invite", text: "Inviting a user" },
    {
      kind: "steps",
      items: [
        "Enter the **email** on the user management screen.",
        "Pick a **role** — PM (full project access) or Administrator (PM access plus user management).",
        "Press **Invite** and a temporary password appears on screen.",
        "**Copy** it and pass it to that person. They change it at first sign-in.",
      ],
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Once you close that dialog the temporary password cannot be seen again.** Copy it and pass it
on before closing. If you lose it, **Reset password** on that user issues a new one.`,
    },
    { kind: "heading", id: "manage-users", text: "Roles, disabling, deleting" },
    {
      kind: "md",
      md: `| Action | Result |
|---|---|
| Change role | PM ↔ Administrator |
| Reset password | Shows a new temporary password once |
| Disable | Keeps the account but blocks sign-in. **Enable** reverses it |
| Delete | Removes the account. Cannot be undone |

Two things are refused. You cannot do any of this to your own account (*You cannot do this to your
own account*), and you cannot do it to the last remaining administrator (*You cannot do this to the
last administrator*). That is what stops an administrator from locking themselves out, or leaving
nobody able to administer anything. To change who administers, **appoint the new administrator
first**.

Deleting a user does not delete the projects they created. Projects are deleted separately, from
the project list.`,
    },
    { kind: "heading", id: "manage-models", text: "Model management" },
    {
      kind: "md",
      md: `The list you define here becomes the model choices on the
[project creation screen](/manual#model).

- **Display name** — what people see.
- **Model ID** — a Bedrock inference profile id. **Model access must be enabled** for that model in
  the deployment region for calls to work. If it is not, the project is created and the first
  conversation fails.
- **Show in the picker** — you can register many models but expose **at most five** as choices.

Removing a model from the list does **not** move projects off it: **projects already created keep
running on the same model.** The list only governs projects created from now on.`,
    },
    { kind: "heading", id: "brand-design", text: "Brand design" },
    {
      kind: "md",
      md: `Prototypes are built with the shadcn/ui defaults. Upload one \`DESIGN.md\` here and **every
prototype built afterwards carries your brand** — use it where the demo only convinces people in the
company's own colours and typeface.

There is **one** profile for the whole deployment (you cannot set a different one per project).`,
    },
    {
      kind: "md",
      md: `There are two ways in. **A design document you already have works as-is** — if it states colours
and fonts in prose or tables, Pathfinder reads them, shows you the tokens it found, and lets you check
and correct them before saving. Starting from scratch is faster with the template.`,
    },
    {
      kind: "steps",
      items: [
        "**Using a document you already have**: pick the file and click **Next**. The tokens found in it appear in a table with colour swatches.",
        "Check the values. **Where the document does not answer, you decide** — if it uses one colour for brand headings and another for buttons, which one becomes `primary` is yours to set.",
        "**Starting from the template**: click **Download template** for an empty `DESIGN.md`, then in the `tokens` block **delete the leading `#`** on the lines you want and replace the values. A line that keeps its `#` stays a comment and is ignored. A document that has a `tokens` block is used as it is (nothing is extracted).",
        "Write guidance on tone, spacing and what to avoid (optional).",
        "Click **Upload**. If the format is wrong it tells you **which line** to fix, so correct it and upload again.",
      ],
    },
    {
      kind: "md",
      md: `Saving with no tokens at all **leaves a warning on the screen.** In that state only the prose
reaches the build agent and the colours stay at the shadcn defaults — sometimes it lands, sometimes it
does not, so tokens are what make the colours certain.`,
    },
    {
      kind: "md",
      md: `| What goes in | Example |
|---|---|
| Colours (11 of them) | \`primary: #5b2ea6\` — \`#rgb\` or \`#rrggbb\` |
| Corner radius | \`radius: 0.75rem\` — \`rem\` or \`px\` |
| Typeface | \`font_sans: Pretendard\` — a font family name |

Leave a line out and that one keeps the shadcn default. A typeface is **named, not shipped** — we do
not bundle a webfont, so a viewer whose browser lacks that font sees the fallback. Say so in the
prose if it genuinely has to be loaded.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**When it takes effect differs per prototype.** Prototypes built **after** you upload the
profile carry the brand immediately. Prototypes that already carry a brand pick up the new values
**the next time they are hosted**. But **prototypes built before you first uploaded a profile do not
change on a re-host** — the brand files were never put into them. For those, one
[**Continue improving**](/manual#complete-card) session applies it.`,
    },
    {
      kind: "md",
      md: `**Replacing** keeps no copy of the previous file. Download the current one first
(**Download original**) if you want to be able to go back.

**Removing** it means prototypes built afterwards use the shadcn defaults again, and prototypes that
already exist fall back to the default look the next time they are hosted.

Every project reads this one file, English-language projects included — so **do not put instructions
about on-screen text language in it** (the project's language setting decides that).`,
    },
  ],
};
