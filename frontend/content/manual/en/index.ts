// frontend/content/manual/en/index.ts — English manual assembly.
//
// 구조는 ko/index.ts와 같아야 한다(절 파일 이름까지). parity.test.ts가 두
// 언어의 블록 구조를 비교하므로, 한쪽에만 절이나 문단을 더하면 즉시 실패한다.
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
