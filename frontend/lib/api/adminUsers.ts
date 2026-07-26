// frontend/lib/api/adminUsers.ts — /admin/users* 클라이언트.
//
// 서버는 username과 email을 둘 다 준다. 화면은 email을 보여주고 액션은 username을
// 보낸다 — 지금은 두 값이 같지만 화면이 그 등식에 의존하지 않게 한다.
import { apiFetch } from "./http";

export type UserRole = "admin" | "pm";

// username은 이메일 형태다("@" 포함). "@"는 URL 경로 세그먼트에서 이스케이프가
// 필요 없는 문자(RFC 3986 pchar)이므로 encodeURIComponent가 만드는 %40을 다시
// 풀어준다 — 그대로 두면 서버는 동일하게 디코드하지만, 리터럴 "@"를 기대하는
// 목(mock) 매처(예: MSW)가 %40과 매칭하지 못한다.
function encodeUsername(username: string): string {
  return encodeURIComponent(username).replace(/%40/g, "@");
}

export interface AdminUser {
  username: string;
  email: string;
  role: UserRole | null;   // 그룹 미배정(반쪽 계정)이면 null
  status: string;          // CONFIRMED / FORCE_CHANGE_PASSWORD / ...
  enabled: boolean;
  created_at: string;
}

export interface InviteResult {
  username: string;
  email: string;
  role: string;
  temp_password: string;
}

export async function listUsers(): Promise<AdminUser[]> {
  const body = await apiFetch<{ users: AdminUser[] }>("/admin/users");
  return body?.users ?? [];
}

export async function inviteUser(email: string, role: UserRole): Promise<InviteResult> {
  const body = await apiFetch<InviteResult>("/admin/users", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
  if (!body) throw new Error("invite returned an empty body");
  return body;
}

export async function resetPassword(
  username: string,
): Promise<{ username: string; temp_password: string }> {
  const body = await apiFetch<{ username: string; temp_password: string }>(
    `/admin/users/${encodeUsername(username)}/reset-password`,
    { method: "POST" },
  );
  if (!body) throw new Error("reset returned an empty body");
  return body;
}

export async function changeRole(username: string, role: UserRole): Promise<void> {
  await apiFetch(`/admin/users/${encodeUsername(username)}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function setUserEnabled(username: string, enabled: boolean): Promise<void> {
  await apiFetch(
    `/admin/users/${encodeUsername(username)}/${enabled ? "enable" : "disable"}`,
    { method: "POST" },
  );
}

export async function deleteUser(username: string): Promise<void> {
  await apiFetch(`/admin/users/${encodeUsername(username)}`, { method: "DELETE" });
}
