// frontend/content/manual/ko/index.ts — 한국어 매뉴얼 조립.
//
// 절마다 파일 하나다. 두 언어를 나란히 고칠 때 diff가 절 단위로 맞아떨어지고,
// 절 하나가 길어져도 다른 절을 읽는 사람을 방해하지 않는다.
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

// 타입이 ManualSectionId를 전부 요구한다 — 절을 추가하고 여기 등록을 잊으면
// 컴파일이 실패한다.
export const ko: ManualContent = {
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
