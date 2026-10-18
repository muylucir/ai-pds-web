"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import {
  changeRole, deleteUser, resetPassword, setUserEnabled,
  type AdminUser, type UserRole,
} from "@/lib/api/adminUsers";
import { TempPasswordPanel } from "./TempPasswordPanel";

// Labels are kept as dictionary keys — module constants cannot call hooks, so they are
// resolved with t(key) at render time (the same convention as canvas's LABEL_KEYS). "PM" is
// the same in both languages but goes in the dictionary anyway: leaving it out would hide which strings are deliberately untranslated.
type T = (key: keyof Dict) => string;

const ROLE_LABEL_KEY: Record<string, keyof Dict> = {
  admin: "admin.roleAdmin",
  pm: "admin.rolePm",
};

function statusLabel(user: AdminUser, t: T): string {
  if (!user.enabled) return t("admin.statusDisabled");
  if (user.status === "FORCE_CHANGE_PASSWORD") return t("admin.statusMustChangePassword");
  if (user.status === "CONFIRMED") return t("admin.statusNormal");
  return user.status;
}

export function UserTable({
  users, currentEmail, onChanged,
}: {
  users: AdminUser[];
  currentEmail: string | null;
  onChanged: () => void;
}) {
  const t = useT();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AdminUser | null>(null);
  const [revealed, setRevealed] = useState<{ email: string; password: string } | null>(null);

  // When the server reports a policy violation (last-admin protection etc.) with a 400, show that
  // sentence as is — if the frontend duplicated the rules, the two places would drift apart.
  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(null);
    }
  }

  async function doReset(user: AdminUser) {
    setBusy(`reset:${user.username}`);
    setError(null);
    try {
      const { temp_password } = await resetPassword(user.username);
      // Put the password on screen first, then refresh the list — reversing the order risks the
      // reload sweeping this panel away before the admin reads it. The server has already
      // switched to FORCE_CHANGE_PASSWORD, so the status column must be refreshed too.
      setRevealed({ email: user.email, password: temp_password });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      {revealed && (
        <TempPasswordPanel email={revealed.email} password={revealed.password}
                           onClose={() => setRevealed(null)} />
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-4 py-3">{t("admin.email")}</th>
              <th className="px-4 py-3">{t("admin.role")}</th>
              <th className="px-4 py-3">{t("admin.colStatus")}</th>
              <th className="px-4 py-3">{t("admin.colCreatedAt")}</th>
              <th className="px-4 py-3">{t("admin.colActions")}</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const isMe = currentEmail === user.email;
              return (
                <tr key={user.username} className="border-t border-slate-100">
                  <td className="px-4 py-3">
                    {user.email}
                    {isMe && <span className="ml-2 text-xs text-violet-600">{t("admin.me")}</span>}
                  </td>
                  <td className="px-4 py-3">
                    {user.role
                      ? <span className={user.role === "admin"
                          ? "rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700"
                          : "rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"}>
                          {t(ROLE_LABEL_KEY[user.role])}
                        </span>
                      : <span className="text-xs text-amber-700">{t("admin.noRole")}</span>}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{statusLabel(user, t)}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {user.created_at.slice(0, 10)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <label className="sr-only" htmlFor={`role-${user.username}`}>
                        {user.email} {t("admin.changeRoleSuffix")}
                      </label>
                      <select
                        id={`role-${user.username}`}
                        value={user.role ?? ""}
                        disabled={busy !== null}
                        onChange={(e) => run(`role:${user.username}`, () =>
                          changeRole(user.username, e.target.value as UserRole))}
                        className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
                      >
                        {!user.role && <option value="">{t("admin.selectRole")}</option>}
                        {/* Using the same word as the badge ("PM"/"관리자") as the option text
                            would make a text query hit two places (badge, option) in the same
                            row, which is ambiguous. value stays "pm"/"admin", so behavior is the same. */}
                        <option value="pm">{t("admin.rolePmOptionShort")}</option>
                        <option value="admin">{t("admin.roleAdminOptionShort")}</option>
                      </select>
                      <button type="button" disabled={busy !== null}
                              onClick={() => doReset(user)}
                              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">
                        {t("admin.resetPassword")}
                      </button>
                      <button type="button" disabled={busy !== null}
                              onClick={() => run(`enabled:${user.username}`, () =>
                                setUserEnabled(user.username, !user.enabled))}
                              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">
                        {user.enabled ? t("admin.disable") : t("admin.enable")}
                      </button>
                      <button type="button" disabled={busy !== null}
                              onClick={() => setConfirmDelete(user)}
                              className="rounded-lg border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50">
                        {t("admin.commonDelete")}
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {confirmDelete && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h3 className="font-bold">{t("admin.deleteUserTitle")}</h3>
            <p className="mt-2 text-sm text-slate-600">
              <strong>{confirmDelete.email}</strong> {t("admin.deleteUserBody")}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirmDelete(null)}
                      className="rounded-lg border border-slate-300 px-4 py-2 text-sm">
                {t("admin.commonCancel")}
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = confirmDelete;
                  setConfirmDelete(null);
                  void run(`delete:${target.username}`,
                           () => deleteUser(target.username));
                }}
                className="rounded-lg bg-rose-600 px-4 py-2 text-sm text-white hover:bg-rose-700"
              >
                {t("admin.commonConfirmDelete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
