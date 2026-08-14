import type { ManualSection } from "../types";

export const admin: ManualSection = {
  id: "admin",
  title: "Administrator screens",
  lede: "Managing users and the model list. Visible only to the administrator role.",
  blocks: [
    {
      kind: "md",
      md: `**User management** and **Model management** are in the user menu (the round icon) at the right
of the header. They do not appear for non-administrators, and typing the address directly sends you
back.`,
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
  ],
};
