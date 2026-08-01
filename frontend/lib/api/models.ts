// frontend/lib/api/models.ts — 모델 카탈로그 클라이언트.
//
// 두 계층이 다른 모양을 받는다: 프로젝트 생성 화면(`listModels`)은 이름과 id만,
// 관리자 화면(`listAdminModels`)은 display 플래그까지. 백엔드가 그렇게 나눠서
// 보내는 이유는 일반 사용자에게 display가 의미가 없기 때문이다 — 여기서
// 필터링하지 않는다.
import { apiFetch } from "./http";

export interface ModelOption {
  name: string;
  model_id: string;
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

export async function addModel(name: string, modelId: string,
                               display: boolean): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>("/admin/models", {
    method: "POST",
    body: JSON.stringify({ name, model_id: modelId, display }),
  });
  return body as AdminModel;
}

export async function patchModel(
  modelId: string, patch: { name?: string; display?: boolean },
): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>(`/admin/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return body as AdminModel;
}

export async function deleteModel(modelId: string): Promise<void> {
  await apiFetch<null>(`/admin/models/${modelId}`, { method: "DELETE" });
}
