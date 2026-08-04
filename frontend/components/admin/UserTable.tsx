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

// 라벨을 딕셔너리 키로 둔다 — 모듈 상수는 훅을 부를 수 없으므로 렌더에서
// t(키)로 푼다(canvas의 LABEL_KEYS와 같은 규약). "PM"은 두 언어에서 같지만
// 딕셔너리에 넣는다: 빼면 어느 문자열이 의도적으로 번역 대상이 아닌지 알 수 없다.
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

  // 서버가 정책 위반(마지막 관리자 보호 등)을 400으로 알려주면 그 문장을 그대로
  // 보여준다 — 프론트가 규칙을 복제하면 두 곳이 어긋난다.
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
      // 비밀번호를 먼저 화면에 올린 뒤 목록을 갱신한다 — 순서를 바꾸면 재로딩이
      // 이 패널을 관리자가 읽기 전에 걷어갈 위험이 있다. 서버는 이미
      // FORCE_CHANGE_PASSWORD로 전환했으므로 상태 컬럼도 갱신해야 한다.
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
                        {/* 배지에 쓰는 것과 같은 단어("PM"/"관리자")를 그대로 옵션 텍스트로
                            쓰면 같은 행 안에서 텍스트 쿼리가 두 곳(배지, 옵션)에 걸려
                            모호해진다. value는 그대로 "pm"/"admin"이라 동작은 같다. */}
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
