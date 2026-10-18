// frontend/lib/api/account.ts — /me* client (your own account).
//
// Why this is split from adminUsers.ts: the authorization models differ. That one has an admin
// handling other people's accounts; here anyone handles only their own. The backend split its
// routers for the same reason (backend/aipds/routes/account.py).
import { apiFetch } from "./http";

/**
 * Changes your own password. Success is 204 (no body).
 *
 * The confirmation input (re-entered new password) is not sent — it is a client-only value,
 * and sending it would give the backend one more field it has no reason to check.
 *
 * No policy check either: Cognito is the sole judge, and two copies of the policy will always
 * drift apart, with the drifted one lying to the user.
 */
export async function changePassword(currentPassword: string,
                                     newPassword: string): Promise<void> {
  await apiFetch<null>("/me/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}
