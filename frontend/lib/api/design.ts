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
}

//: 다운로드는 앵커로 연다 — 같은 오리진 프록시가 쿠키를 Bearer로 바꿔 준다.
export const DESIGN_RAW_PATH = "/api/admin/design/raw";
export const DESIGN_TEMPLATE_PATH = "/api/admin/design/template";

export async function getDesignProfile(): Promise<DesignProfile | null> {
  const body = await apiFetch<{ profile: DesignProfile | null }>("/admin/design");
  return body?.profile ?? null;
}

export async function uploadDesignProfile(file: File): Promise<DesignProfile> {
  const form = new FormData();
  form.append("file", file);
  const body = await apiFetch<{ profile: DesignProfile }>("/admin/design", {
    method: "PUT",
    body: form,
  });
  return (body as { profile: DesignProfile }).profile;
}

export async function deleteDesignProfile(): Promise<void> {
  await apiFetch<null>("/admin/design", { method: "DELETE" });
}
