// Shared fetch wrapper. Extracted so surveys.ts/prototypes.ts don't each carry
// a copy (client.ts's own request() is unexported and assumes a JSON body,
// which 204 responses don't have).
import { API_BASE_URL, apiErrorFrom } from "./client";
import { CREDENTIALS } from "@/lib/auth";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  const headers: Record<string, string> = {
    // Do not set Content-Type on a FormData body — the browser generates the boundary, and if
    // we hard-coded application/json the server could not read the multipart.
    ...(init?.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: CREDENTIALS,
  });
  // The error body is read by apiErrorFrom in client.ts — if each wrapper read it separately, one
  // would miss one of the shapes of detail (observed: an array went through String() and showed "[object Object]").
  if (!res.ok) throw await apiErrorFrom(res);
  if (res.status === 204) return null;
  return (await res.json()) as T;
}
