"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { useT } from "@/lib/i18n/provider";
import { inviteUser, type InviteResult, type UserRole } from "@/lib/api/adminUsers";
import { TempPasswordPanel } from "./TempPasswordPanel";

// New sign-ups are possible only by invitation (the pool blocks self-signup). This modal is that entry point.
export function InviteUserModal({
  onInvited, onClose,
}: {
  onInvited: () => void;
  onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRole>("pm");
  const [busy, setBusy] = useState(false);
  const t = useT();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InviteResult | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const invited = await inviteUser(email.trim(), role);
      setResult(invited);
      // Refresh the list right away but keep the modal open — it has to show the password.
      onInvited();
    } catch (err) {
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
        <h2 className="text-lg font-bold">{t("admin.inviteUserTitle")}</h2>
        {result ? (
          <div className="mt-4">
            <TempPasswordPanel email={result.email} password={result.temp_password}
                               onClose={onClose} />
          </div>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-4">
            <div>
              <label htmlFor="invite-email" className="block text-sm font-medium">
                {t("admin.email")}
              </label>
              <input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                placeholder="user@example.com"
              />
            </div>
            <div>
              <label htmlFor="invite-role" className="block text-sm font-medium">
                {t("admin.role")}
              </label>
              <select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as UserRole)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="pm">{t("admin.rolePmOption")}</option>
                <option value="admin">{t("admin.roleAdminOption")}</option>
              </select>
            </div>
            {error && <p className="text-sm text-rose-600">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose}
                      className="rounded-lg border border-slate-300 px-4 py-2 text-sm">
                {t("admin.commonCancel")}
              </button>
              <button type="submit" disabled={busy}
                      className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                {busy ? t("admin.inviting") : t("admin.invite")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
