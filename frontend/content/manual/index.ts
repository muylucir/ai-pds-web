// frontend/content/manual/index.ts — the single source for looking up manual content.
//
// Same shape as dictFor() in lib/i18n/index.ts. The manual follows the **UI locale**
// (not the project's document language) — the manual is text that describes the screen, so it
// must be in the same language as the screen.
import type { Locale } from "@/lib/i18n";

import { ko } from "./ko";
import { en } from "./en";
import type { ManualContent, ManualSectionId } from "./types";

// The **display order** of the table of contents and the body. It is a value rather than a type because moving a
// section should not be a type change. Instead of `readonly [...]`, satisfies is applied to an array
// literal, so that omitting an id fails compilation.
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

// Checks that the array above covers **all** of ManualSectionId. satisfies only guarantees "each item
// is an id" and cannot catch omissions, so if any id is missing this type
// stops being never and the assertion below fails to compile.
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
