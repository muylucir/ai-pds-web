import type { ManualSection } from "../types";

export const createProject: ManualSection = {
  id: "create-project",
  title: "Creating a project",
  lede: "One Discovery is one project. Two of the values you set here cannot be changed later.",
  blocks: [
    {
      kind: "mockup",
      id: "project-create",
      caption: "The creation form at the top of the project list screen",
    },
    {
      kind: "steps",
      items: [
        "**Project ID** — letters, digits, hyphen (`-`) and underscore (`_`) only. No spaces, and an ID that already exists is rejected.",
        "**Project name** — optional. Leave it blank and the list shows the ID instead.",
        "**Default model** — the AI model this project runs on.",
        "**Document language** — the language of the documents, prototypes and chat it will produce.",
        "Press **Create project** and you land directly on that project's dashboard.",
      ],
    },
    {
      kind: "md",
      md: `The ID goes into the URL (\`/projects/{ID}/workspace\`). Put the human-readable title in the
name field and keep the ID short.`,
    },
    { kind: "heading", id: "model", text: "Choosing the model" },
    {
      kind: "md",
      md: `What appears in this list is up to your administrator ([model management](/manual#manage-models)).
The model you pick is **pinned to that project** — if the administrator later removes it from the
list, projects already created keep running on it.

The badge in the header tells you which model the project you are looking at runs on.`,
    },
    { kind: "heading", id: "doc-language", text: "Document language" },
    {
      kind: "md",
      md: `This sets the language of **everything the AI produces** — the Discovery documents, the
question sheets, the wording inside the prototype, what the AI says in chat, and the survey
questions.

It is entirely separate from the screen language (한국어/English in the header). Open a Korean
project with an English interface and the buttons are English while the documents are Korean.
That is correct, and the language badge in the header shows it.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**Document language can only be set at creation time.** There is no screen for changing it
afterwards — you would have to create a new project. Check this value before you start.`,
    },
    { kind: "heading", id: "delete-project", text: "Deleting a project" },
    {
      kind: "md",
      md: `**Delete** in the list opens a confirmation. More than the project entry goes away:

- The whole chat history and every document written
- Prototype build output and any running preview
- Validation surveys and **the responses already collected**
- Preview links and survey links you shared (they stop working immediately)

This cannot be undone. If anything matters, download the documents as \`.zip\` and the survey
responses as CSV first.`,
    },
  ],
};
