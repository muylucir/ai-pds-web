// frontend/lib/api/transfer.ts — 프로젝트 이관(내보내기·가져오기) 클라이언트.
//
// 가져오기는 세 단계다: presign 요청 → **브라우저가 S3로 직접 PUT** → 임포트 요청.
// 두 번째 단계가 우리 서버를 지나지 않는 것이 요점이다 — nginx의
// `client_max_body_size`(6m)를 올리려면 인스턴스를 교체해야 하고, 교체는 그 박스의
// 프로토타입 빌드 트리를 가져간다.
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

// ---- 내보내기 ----

/** 기본 파일명. 서버가 `Content-Disposition`으로 이름을 주지만, 프록시를 지나며
 *  그 헤더가 없을 수도 있으므로 폴백을 갖는다. */
function fallbackFilename(projectId: string): string {
  return `aipds-export-${projectId}.zip`;
}

/** `Content-Disposition`에서 파일명을 뽑는다. 없으면 null.
 *
 *  `filename*=UTF-8''…`(RFC 5987)를 먼저 본다 — 백엔드가 한글 프로젝트 id를 위해
 *  그 형태를 함께 싣고, ASCII 폴백인 `filename="…"`은 한글이 하이픈으로 뭉개진
 *  이름이다. 순서를 뒤집으면 한국어 사용자가 항상 뭉개진 이름을 받는다. */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      /* 잘못 인코딩된 값 — 아래 ASCII 폼으로 떨어진다 */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : null;
}

/** 번들을 받아 브라우저 다운로드로 넘긴다.
 *
 *  **앵커 내비게이션이 아니라 fetch다.** `<a href>`로 열면 브라우저가
 *  `Content-Disposition`을 알아서 처리해 편하지만, 실패 응답(빌드 세션 중 409)이
 *  JSON 페이지로 떠서 사용자가 무슨 일인지 알 수 없다. 진행 중인 빌드는 흔한
 *  상태이므로 그 오류는 화면 문구로 나와야 한다.
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

// ---- 가져오기 ----

/** 번들을 S3에 올리고 `upload_id`를 돌려준다.
 *
 *  `finishImport`와 나눠 둔 이유는 409다: 대상 id가 이미 있으면 화면이 새 id를
 *  받아 **같은 upload_id로** 다시 부른다 — 사용자가 번들을 다시 올리지 않는다.
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

/** presigned URL에 PUT한다.
 *
 *  **`XMLHttpRequest`인 이유는 진행률이다.** `fetch`는 업로드 진행을 보고하지
 *  않는다(ReadableStream 업로드는 HTTP/2 전용이고 지원이 고르지 않다). 워크숍
 *  네트워크에서 수백 MB를 올리는 동안 진행 표시가 없으면 멈춘 것과 구별되지 않는다.
 *
 *  **자격증명을 보내지 않는다.** 이 요청의 인증은 URL 안의 서명이다. `credentials`를
 *  붙이면 S3는 `Access-Control-Allow-Credentials`를 돌려주지 않으므로 브라우저가
 *  CORS 검사에서 응답을 버린다 — `lib/api/client.ts`의 헬퍼를 타면 안 되는 이유가
 *  그것이다(그쪽은 모든 호출에 `credentials: "include"`를 붙인다).
 */
function putToStorage(ticket: UploadTicket, file: File,
                      onProgress?: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", ticket.url, true);
    // 서명에 포함된 헤더다 — 값이 다르면 S3가 403을 준다.
    xhr.setRequestHeader("Content-Type", ticket.content_type);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      // S3의 거절은 XML이다. 그 본문을 화면에 흘리지 않는다 — 사용자가 할 일은
      // 다시 시도하는 것이고, 원인(서명 만료, CORS 미설정)은 개발자의 몫이다.
      else reject(new ApiError(xhr.status, "upload_failed"));
    };
    xhr.onerror = () => reject(new ApiError(0, "upload_failed"));
    xhr.onabort = () => reject(new ApiError(0, "upload_failed"));
    xhr.send(file);
  });
}

/** 올려 둔 번들을 프로젝트로 되살린다. `projectId`가 없으면 번들의 원본 id를 쓴다. */
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
