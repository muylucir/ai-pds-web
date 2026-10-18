// frontend/content/manual/en/index.ts — English manual assembly.
//
// The structure must match ko/index.ts (down to section file names). parity.test.ts compares the
// block structure of the two languages, so adding a section or paragraph to only one side fails immediately.
import type { ManualContent } from "../types";

import { intro } from "./intro";
import { gettingStarted } from "./getting-started";
import { createProject } from "./create-project";
import { workspace } from "./workspace";
import { questions } from "./questions";
import { review } from "./review";
import { prototypes } from "./prototypes";
import { survey } from "./survey";
import { dashboard } from "./dashboard";
import { admin } from "./admin";
import { operations } from "./operations";

export const en: ManualContent = {
  intro,
  "getting-started": gettingStarted,
  "create-project": createProject,
  workspace,
  questions,
  review,
  prototypes,
  survey,
  dashboard,
  admin,
  operations,
};
