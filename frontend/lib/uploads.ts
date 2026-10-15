// frontend/lib/uploads.ts — 첨부 업로드의 규칙(받는 형식·크기)과 실패 이유.
//
// 받는 확장자와 크기 상한은 백엔드(backend/aipds/parsers/uploads.py의 ALLOWED,
// MAX_UPLOAD_BYTES)가 정본이다. 여기 두 벌을 두는 이유는 올리기 **전에** 거르기
// 위해서다 — 10MB를 다 보낸 뒤에야 "지원하지 않는 형식"을 듣지 않게. 두 값이
// 같은지는 backend/tests/test_uploads_parser.py가 대조한다.
import { ApiError } from "@/lib/api/client";
import type { Dict } from "@/lib/i18n";

/** 받는 확장자를 사용자에게 보일 묶음으로. 첨부 모달의 안내가 이 순서대로 그린다. */
export const UPLOAD_GROUPS = [
  { label: "upload.groupDocs",
    extensions: [".md", ".txt", ".csv", ".xlsx", ".docx", ".pptx", ".hwpx", ".pdf"] },
  { label: "upload.groupWeb", extensions: [".html", ".htm"] },
  { label: "upload.groupTranscripts", extensions: [".vtt", ".srt"] },
  { label: "upload.groupImages", extensions: [".png", ".jpg", ".jpeg", ".gif", ".webp"] },
] as const satisfies readonly { label: keyof Dict; extensions: readonly string[] }[];

export const UPLOAD_EXTENSIONS: readonly string[] = UPLOAD_GROUPS.flatMap((g) => g.extensions);

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** 받지 않지만 새 형식으로 다시 저장하면 되는 것 — 그 안내를 따로 한다. */
const LEGACY_EXTENSIONS = [".doc", ".ppt", ".xls", ".hwp"];

/** `<input type="file" accept>` 값. */
export const UPLOAD_ACCEPT = UPLOAD_EXTENSIONS.join(",");

export type UploadProblem = "legacy" | "type" | "size" | "expanded" | "damaged" | "failed";

/** 문구 키. 백엔드는 UI 언어를 모르므로 이유를 코드로 바꿔 여기서 고른다. */
export const UPLOAD_PROBLEM_KEY: Record<UploadProblem, keyof Dict> = {
  legacy: "upload.problemLegacy",
  type: "upload.problemType",
  size: "upload.problemSize",
  expanded: "upload.problemExpanded",
  damaged: "upload.problemDamaged",
  failed: "upload.problemFailed",
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** 올리기 전에 알 수 있는 문제. 없으면 null. */
export function checkUpload(file: File): UploadProblem | null {
  const ext = extensionOf(file.name);
  if (LEGACY_EXTENSIONS.includes(ext)) return "legacy";
  if (!UPLOAD_EXTENSIONS.includes(ext)) return "type";
  if (file.size > MAX_UPLOAD_BYTES) return "size";
  return null;
}

/** 업로드 실패 → 이유. 413은 nginx가 낼 수도 있다(본문이 HTML이다) — 상태로만 본다.
 *  415의 본문은 backend/aipds/parsers/uploads.py가 쓰는 문장이다. */
export function problemFromError(err: unknown): UploadProblem {
  if (!(err instanceof ApiError)) return "failed";
  if (err.status === 413) return "size";
  if (err.status === 415) {
    if (err.detail.includes("expands beyond")) return "expanded";
    if (err.detail.includes("unsupported extension")) return "type";
    return "damaged";
  }
  return "failed";
}

/** 실패 이유의 문장. `{mb}`는 상한이다 — 숫자를 문구 두 벌에 박지 않는다. */
export function uploadProblemText(t: (key: keyof Dict) => string, problem: UploadProblem): string {
  return t(UPLOAD_PROBLEM_KEY[problem]).replace("{mb}", String(MAX_UPLOAD_BYTES / (1024 * 1024)));
}
