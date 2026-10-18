// frontend/lib/api/adminUsers.ts — client for /admin/users*.
//
// The server returns both username and email. The screen shows email and actions send username
// — the two values are currently equal, but the screen must not depend on that equality.
import { apiFetch } from "./http";

export type UserRole = "admin" | "pm";

// The username is email-shaped (contains "@"). "@" is a character that needs no escaping in a URL path
// segment (RFC 3986 pchar), so the %40 produced by encodeURIComponent is decoded back —
// left as is, the server decodes it identically, but mock matchers expecting a literal "@"
// (e.g., MSW) fail to match %40.
function encodeUsername(username: string): string {
  return encodeURIComponent(username).replace(/%40/g, "@");
}

export interface AdminUser {
  username: string;
  email: string;
  role: UserRole | null;   // null if no group is assigned (a half-created account)
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

/** Gives the project's ownership to a user (email or username). The previous owner remains as a viewing
 *  member. It must be an account that can log in (active, with a role). */
export async function assignProjectOwner(
  pid: string, owner: string,
): Promise<{ owner: string; members: string[] }> {
  const body = await apiFetch<{ owner: string; members: string[] }>(
    `/admin/projects/${encodeURIComponent(pid)}/owner`,
    { method: "PUT", body: JSON.stringify({ owner }) },
  );
  if (!body) throw new Error("owner assignment returned an empty body");
  return body;
}
