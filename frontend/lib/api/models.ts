// frontend/lib/api/models.ts — 모델 카탈로그 클라이언트.
//
// 두 계층이 다른 모양을 받는다: 프로젝트 생성 화면(`listModels`)은 이름과 id만,
// 관리자 화면(`listAdminModels`)은 display 플래그까지. 백엔드가 그렇게 나눠서
// 보내는 이유는 일반 사용자에게 display가 의미가 없기 때문이다 — 여기서
// 필터링하지 않는다.
import { apiFetch } from "./http";

// Claude Code CLI의 `--effort` 값. 백엔드 model_catalog.Effort와 같은 집합이어야 한다 —
// 밖의 값은 서버가 422로 거부한다.
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export type Effort = (typeof EFFORTS)[number];

export interface ModelOption {
  name: string;
  model_id: string;
  // 모델과 짝으로 정한 effort. null = CLI 기본값. 구 백엔드 응답에는 없을 수 있다.
  effort?: Effort | null;
}

export interface AdminModel extends ModelOption {
  display: boolean;
}

// model_id는 영숫자와 `.`·`-`·`:`만 포함하므로 경로 세그먼트에서 이스케이프가
// 필요 없다(adminUsers.ts의 "@" 처리 같은 것이 없는 이유).
export async function listModels(): Promise<ModelOption[]> {
  const body = await apiFetch<{ models: ModelOption[] }>("/models");
  return body?.models ?? [];
}

export async function listAdminModels(): Promise<AdminModel[]> {
  const body = await apiFetch<{ models: AdminModel[] }>("/admin/models");
  return body?.models ?? [];
}

export async function addModel(name: string, modelId: string, display: boolean,
                               effort: Effort | null = null): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>("/admin/models", {
    method: "POST",
    body: JSON.stringify({ name, model_id: modelId, display, effort }),
  });
  return body as AdminModel;
}

// `effort: null`은 CLI 기본값으로 비운다. 키를 빼면 서버가 그대로 둔다.
export async function patchModel(
  modelId: string, patch: { name?: string; display?: boolean; effort?: Effort | null },
): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>(`/admin/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return body as AdminModel;
}

// 등록된 모델 전체의 새 순서. 일부만 보내면 서버가 409로 거부한다
// (model_catalog.reorder — 관리자가 본 적 없는 순서를 저장하지 않는다).
export async function reorderModels(modelIds: string[]): Promise<AdminModel[]> {
  const body = await apiFetch<{ models: AdminModel[] }>("/admin/models/order", {
    method: "PUT",
    body: JSON.stringify({ model_ids: modelIds }),
  });
  return body?.models ?? [];
}

export async function deleteModel(modelId: string): Promise<void> {
  await apiFetch<null>(`/admin/models/${modelId}`, { method: "DELETE" });
}
