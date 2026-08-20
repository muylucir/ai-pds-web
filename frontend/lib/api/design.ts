// frontend/lib/api/design.ts — 브랜드 디자인 프로필(관리자 전용).
//
// 파싱은 백엔드 한 곳에만 있다 — 여기서 미리 검증하지 않는다. 파서가 두 벌이
// 되면 어긋나고, 어긋난 쪽이 화면이면 admin은 통과한 줄 알고 넘어간다.
import { apiFetch } from "./http";

export interface DesignProfile {
  filename: string;
  uploaded_at: string;
  uploaded_by: string;
  tokens: Record<string, string>;
  prose: string;
  //: 저장물이 아니다 — 백엔드가 프로필에서 유도한다(`no-tokens`). 그래서 업로드
  //  응답과 다시 열었을 때의 응답이 같은 말을 한다.
  //
  //  optional인 것은 배포 순서 때문이다: 프런트가 먼저 갱신되고 백엔드가 아직
  //  구버전인 창에서는 이 필드가 없이 온다. required로 두고 `.includes()`를 부르면
  //  그 창에서 관리 페이지가 통째로 죽는다(에러 바운더리가 없다).
  warnings?: string[];
}

//: 저장 전에 "이 문서에서 어떤 토큰이 나오는가"만 묻는 결과.
//  `origin`: 문서에 이미 펜스가 있었는지(`fence`), 모델이 산문에서 뽑았는지
//  (`extracted`), 아무것도 못 찾았는지(`none`).
export interface DesignPreview {
  tokens: Record<string, string>;
  origin: "fence" | "extracted" | "none";
  warnings: string[];
}

//: 다운로드는 앵커로 연다 — 같은 오리진 프록시가 쿠키를 Bearer로 바꿔 준다.
export const DESIGN_RAW_PATH = "/api/admin/design/raw";
export const DESIGN_TEMPLATE_PATH = "/api/admin/design/template";

export async function getDesignProfile(): Promise<DesignProfile | null> {
  const body = await apiFetch<{ profile: DesignProfile | null }>("/admin/design");
  return body?.profile ?? null;
}

export async function previewDesignProfile(file: File): Promise<DesignPreview> {
  const form = new FormData();
  form.append("file", file);
  const body = await apiFetch<DesignPreview>("/admin/design/preview", {
    method: "POST",
    body: form,
  });
  return body as DesignPreview;
}

export async function uploadDesignProfile(
  file: File, tokens?: Record<string, string>,
): Promise<DesignProfile> {
  const form = new FormData();
  form.append("file", file);
  // 확인된 토큰만 보낸다. 서버가 이 값을 원문에 ```tokens 블록으로 심어 저장하므로
  // (파생값을 따로 저장하지 않는다), 다음번에 원문을 내려받으면 이 값이 파일에
  // 들어 있다. 문서에 이미 펜스가 있으면 서버가 이 필드를 무시한다 — 펜스가 권위다.
  if (tokens && Object.keys(tokens).length > 0) {
    form.append("tokens", JSON.stringify(tokens));
  }
  const body = await apiFetch<{ profile: DesignProfile }>("/admin/design", {
    method: "PUT",
    body: form,
  });
  return (body as { profile: DesignProfile }).profile;
}

export async function deleteDesignProfile(): Promise<void> {
  await apiFetch<null>("/admin/design", { method: "DELETE" });
}
