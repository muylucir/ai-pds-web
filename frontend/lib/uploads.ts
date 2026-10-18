// frontend/lib/uploads.ts — rules for attachment uploads (accepted formats, size) and failure reasons.
//
// The backend (ALLOWED and MAX_UPLOAD_BYTES in backend/aipds/parsers/uploads.py) is the
// source of truth for the accepted extensions and the size cap. A second copy lives here to
// filter **before** uploading — so the user does not hear "unsupported format" only after sending
// all 10MB. backend/tests/test_uploads_parser.py checks that the two match.
import { ApiError } from "@/lib/api/client";
import type { Dict } from "@/lib/i18n";

/** Accepted extensions grouped for display to the user. The attachment modal's hint draws them in this order. */
export const UPLOAD_GROUPS = [
  { label: "upload.groupDocs",
    extensions: [".md", ".txt", ".csv", ".xlsx", ".docx", ".pptx", ".hwpx", ".pdf"] },
  { label: "upload.groupWeb", extensions: [".html", ".htm"] },
  { label: "upload.groupTranscripts", extensions: [".vtt", ".srt"] },
  { label: "upload.groupImages", extensions: [".png", ".jpg", ".jpeg", ".gif", ".webp"] },
] as const satisfies readonly { label: keyof Dict; extensions: readonly string[] }[];

export const UPLOAD_EXTENSIONS: readonly string[] = UPLOAD_GROUPS.flatMap((g) => g.extensions);

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** Not accepted, but fixed by re-saving in a newer format — that hint is shown separately. */
const LEGACY_EXTENSIONS = [".doc", ".ppt", ".xls", ".hwp"];

/** The `<input type="file" accept>` value. */
export const UPLOAD_ACCEPT = UPLOAD_EXTENSIONS.join(",");

export type UploadProblem = "legacy" | "type" | "size" | "expanded" | "damaged" | "failed";

/** Message keys. The backend does not know the UI language, so reasons are turned into codes and picked here. */
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

/** A problem detectable before uploading. null if none. */
export function checkUpload(file: File): UploadProblem | null {
  const ext = extensionOf(file.name);
  if (LEGACY_EXTENSIONS.includes(ext)) return "legacy";
  if (!UPLOAD_EXTENSIONS.includes(ext)) return "type";
  if (file.size > MAX_UPLOAD_BYTES) return "size";
  return null;
}

/** Upload failure → reason. nginx can also return 413 (with an HTML body) — judge by status only.
 *  The 415 body is the sentence backend/aipds/parsers/uploads.py writes. */
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

/** The sentence for a failure reason. `{mb}` is the cap — the number is not baked into two copies of the text. */
export function uploadProblemText(t: (key: keyof Dict) => string, problem: UploadProblem): string {
  return t(UPLOAD_PROBLEM_KEY[problem]).replace("{mb}", String(MAX_UPLOAD_BYTES / (1024 * 1024)));
}
