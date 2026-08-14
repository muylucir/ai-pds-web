// frontend/content/manual/index.ts — 매뉴얼 콘텐츠 조회의 단일 출처.
//
// lib/i18n/index.ts의 dictFor()와 같은 모양이다. 매뉴얼은 **UI 로케일**을
// 따른다(프로젝트의 문서 언어가 아니다) — 매뉴얼은 화면을 설명하는 글이므로
// 화면과 같은 언어여야 한다.
import type { Locale } from "@/lib/i18n";

import { ko } from "./ko";
import { en } from "./en";
import type { ManualContent, ManualSectionId } from "./types";

// 목차와 본문의 **표시 순서**. 타입이 아니라 값으로 두는 이유는 절을 옮기는
// 것이 타입 변경이 아니어야 하기 때문이다. `readonly [...]`가 아니라 배열
// 리터럴에 satisfies를 걸어, id를 빠뜨리면 컴파일이 실패하게 한다.
export const MANUAL_ORDER = [
  "intro",
  "getting-started",
  "create-project",
  "workspace",
  "questions",
  "review",
  "prototypes",
  "survey",
  "dashboard",
  "admin",
  "operations",
] as const satisfies readonly ManualSectionId[];

// 위 배열이 ManualSectionId를 **전부** 덮는지 검사한다. satisfies는 "각 항목이
// id다"까지만 보장하고 누락은 잡지 못하므로, 빠진 id가 있으면 이 타입이
// never가 아니게 되어 아래 단정에서 컴파일 에러가 난다.
type Missing = Exclude<ManualSectionId, (typeof MANUAL_ORDER)[number]>;
const _allSectionsOrdered: Missing extends never ? true : never = true;
void _allSectionsOrdered;

export function manualFor(locale: Locale): ManualContent {
  return locale === "en" ? en : ko;
}

export type {
  DiagramNode,
  ManualBlock,
  ManualContent,
  ManualDiagramBlock,
  ManualSection,
  ManualSectionId,
} from "./types";
export type { DiagramId, MockupId } from "./visuals";
