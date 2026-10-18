// frontend/lib/api/transfer.ts — client for project transfer (export and import).
//
// Import has three steps: presign request -> **browser PUTs directly to S3** -> import request.
// The point is that the second step does not pass through our server -- a bundle has no
// upper size bound, and requests through the server hit the body limits of nginx and the
// Next middleware (values sized for attachment uploads).
import { API_BASE_URL, ApiError } from "./client";
import { CREDENTIALS } from "@/lib/auth";
import { apiFetch } from "./http";

export interface ImportWarning {
  code: string;
  count: number;
}

export interface ImportResult {
  project_id: string;
  name: string | null;
  language: string;
  model_id: string | null;
  source_project_id: string;
  counts: { objects: number; source_files: number; prototypes: number };
  warnings: ImportWarning[];
}

interface UploadTicket {
  upload_id: string;
  url: string;
  content_type: string;
  expires_in: number;
  max_bytes: number;
}

// ---- Export ----

/** Default filename. The server supplies the name via `Content-Disposition`, but that
 *  header may be missing after passing through the proxy, so keep a fallback. */
function fallbackFilename(projectId: string): string {
  return `aipds-export-${projectId}.zip`;
}

/** Extract the filename from `Content-Disposition`. null if absent.
 *
 *  Look at `filename*=UTF-8''…` (RFC 5987) first -- the backend includes that form for
 *  Korean project ids, and the ASCII fallback `filename="…"` is a name with the Hangul
 *  collapsed into hyphens. Reverse the order and Korean users always get the mangled name. */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      /* badly encoded value — fall through to the ASCII form below */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : null;
}

/** Fetch the bundle and hand it to the browser as a download.
 *
 *  **fetch, not anchor navigation.** Opening via `<a href>` is convenient because the
 *  browser handles `Content-Disposition` itself, but a failure response (409 during a
 *  build session) then shows up as a JSON page and the user cannot tell what happened.
 *  A build in progress is a common state, so that error must appear as on-screen text.
 */
export async function exportProject(projectId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/export`,
    { credentials: CREDENTIALS });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filenameFromDisposition(res.headers.get("content-disposition"))
      ?? fallbackFilename(projectId);
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ---- Import ----

/** Upload the bundle to S3 and return the `upload_id`.
 *
 *  Split from `finishImport` because of 409: if the target id already exists, the screen
 *  takes a new id and calls again **with the same upload_id** -- the user does not
 *  re-upload the bundle.
 */
export async function stageBundle(
  file: File, onProgress?: (fraction: number) => void,
): Promise<string> {
  const ticket = await apiFetch<UploadTicket>("/project-imports/uploads", {
    method: "POST",
    body: JSON.stringify({ size_bytes: file.size }),
  });
  if (!ticket) throw new ApiError(500, "no upload ticket");
  await putToStorage(ticket, file, onProgress);
  return ticket.upload_id;
}

/** PUT to the presigned URL.
 *
 *  **`XMLHttpRequest` because of progress.** `fetch` does not report upload progress
 *  (ReadableStream uploads are HTTP/2-only and unevenly supported). While uploading
 *  hundreds of MB over a workshop network, no progress indicator is indistinguishable
 *  from a hang.
 *
 *  **No credentials are sent.** This request is authenticated by the signature in the
 *  URL. With `credentials` attached, S3 does not return `Access-Control-Allow-Credentials`,
 *  so the browser drops the response in the CORS check -- that is why this must not go
 *  through the helpers in `lib/api/client.ts` (they attach `credentials: "include"` to
 *  every call).
 */
function putToStorage(ticket: UploadTicket, file: File,
                      onProgress?: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", ticket.url, true);
    // This header is part of the signature -- if the value differs, S3 returns 403.
    xhr.setRequestHeader("Content-Type", ticket.content_type);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      // S3 rejections are XML. Do not leak that body onto the screen -- the user's
      // next step is to retry, and the cause (expired signature, missing CORS) is
      // for developers.
      else reject(new ApiError(xhr.status, "upload_failed"));
    };
    xhr.onerror = () => reject(new ApiError(0, "upload_failed"));
    xhr.onabort = () => reject(new ApiError(0, "upload_failed"));
    xhr.send(file);
  });
}

/** Restore the uploaded bundle as a project. Without `projectId`, the bundle's original id is used. */
export async function finishImport(uploadId: string,
                                   projectId?: string): Promise<ImportResult> {
  const body: { upload_id: string; project_id?: string } = { upload_id: uploadId };
  if (projectId) body.project_id = projectId;
  const result = await apiFetch<ImportResult>("/project-imports", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!result) throw new ApiError(500, "import_failed");
  return result;
}
