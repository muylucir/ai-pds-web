// Shared fetch wrapper. Extracted so surveys.ts/prototypes.ts don't each carry
// a copy (client.ts's own request() is unexported and assumes a JSON body,
// which 204 responses don't have).
import { API_BASE_URL, ApiError } from "./client";
import { getAuthToken } from "@/lib/auth";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...(token ? { "X-Project-Token": token } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
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
  if (res.status === 204) return null;
  return (await res.json()) as T;
}
