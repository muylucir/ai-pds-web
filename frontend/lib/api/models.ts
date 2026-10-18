// frontend/lib/api/models.ts — model catalog client.
//
// The two layers receive different shapes: the project creation screen (`listModels`) gets only names and ids,
// and the admin screen (`listAdminModels`) gets the display flag as well. The backend splits them that way
// because display has no meaning for regular users — we do not
// filter here.
import { apiFetch } from "./http";

// The Claude Code CLI's `--effort` values. Must be the same set as the backend's model_catalog.Effort —
// the server rejects values outside it with a 422.
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export type Effort = (typeof EFFORTS)[number];

export interface ModelOption {
  name: string;
  model_id: string;
  // The effort chosen as a pair with the model. null = CLI default. May be absent in old backend responses.
  effort?: Effort | null;
}

export interface AdminModel extends ModelOption {
  display: boolean;
}

// model_id contains only alphanumerics and `.`, `-`, `:`, so it needs no escaping in a path segment
// (which is why there is nothing like the "@" handling in adminUsers.ts).
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

// `effort: null` clears it to the CLI default. Omitting the key makes the server leave it as is.
export async function patchModel(
  modelId: string, patch: { name?: string; display?: boolean; effort?: Effort | null },
): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>(`/admin/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return body as AdminModel;
}

// The new order of all registered models. Sending only some makes the server reject it with a 409
// (model_catalog.reorder — it does not save an order the admin has never seen).
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
