import type { ManualSection } from "../types";

export const gettingStarted: ManualSection = {
  id: "getting-started",
  title: "Getting started",
  lede: "Signing in, roles, and screen language. Things you check once and then forget.",
  blocks: [
    {
      kind: "steps",
      items: [
        "Open the address your administrator gave you.",
        "Press **Sign in** to go to the authentication screen and enter your email and password.",
        "The first time you sign in with a temporary password, you are asked to set a new one.",
        "After signing in, the **project list** is your home screen.",
      ],
    },
    {
      kind: "md",
      md: `Accounts are created **by invitation only** — there is no self-service sign-up.
If you do not have one, ask an administrator to invite you. They receive a temporary
password shown exactly once and pass it on to you.`,
    },
    { kind: "heading", id: "roles", text: "Roles" },
    {
      kind: "md",
      md: `| Role | What it can do |
|---|---|
| PM | Everything about projects — create, run Discovery, build prototypes, run surveys, delete |
| Administrator | Everything a PM can do, plus inviting and managing users and the model list |

Only administrators can change roles, and no one can change their own role.`,
    },
    { kind: "heading", id: "ui-language", text: "Screen language" },
    {
      kind: "md",
      md: `The **한국어 / English** buttons at the right of the header switch the screen language.
Your choice is remembered in the browser for a year, so you only pick it once. This manual
follows the same buttons.

The screen language is separate from the language a project **writes its documents in** —
see [document language](/manual#doc-language).`,
    },
    {
      kind: "callout",
      tone: "note",
      md: `Screen language is **per reader**. One person can view a project with a Korean interface
while another views the same project in English, and the documents still come out in the one
language the project was created with.`,
    },
  ],
};
