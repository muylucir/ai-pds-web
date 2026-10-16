// Shared fetch wrapper. Extracted so surveys.ts/prototypes.ts don't each carry
// a copy (client.ts's own request() is unexported and assumes a JSON body,
// which 204 responses don't have).
import { API_BASE_URL, apiErrorFrom } from "./client";
import { CREDENTIALS } from "@/lib/auth";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  const headers: Record<string, string> = {
    // FormData 본문에는 Content-Type을 붙이지 않는다 — boundary는 브라우저가
    // 만들고, 우리가 application/json을 박으면 서버가 multipart를 못 읽는다.
    ...(init?.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: CREDENTIALS,
  });
  // 오류 본문은 client.ts의 apiErrorFrom이 읽는다 — 래퍼마다 따로 읽으면 한쪽이 detail의
  // 모양 하나를 놓친다(실측: 배열을 String()으로 바꿔 "[object Object]"를 보였다).
  if (!res.ok) throw await apiErrorFrom(res);
  if (res.status === 204) return null;
  return (await res.json()) as T;
}
