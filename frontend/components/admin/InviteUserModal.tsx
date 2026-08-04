"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { useT } from "@/lib/i18n/provider";
import { inviteUser, type InviteResult, type UserRole } from "@/lib/api/adminUsers";
import { TempPasswordPanel } from "./TempPasswordPanel";

// 신규 가입은 초대로만 가능하다(풀이 self-signup을 막는다). 이 모달이 그 창구다.
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
      // 목록은 곧바로 갱신하되 모달은 닫지 않는다 — 비밀번호를 보여줘야 한다.
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
        <h2 className="text-lg font-bold">사용자 초대</h2>
        {result ? (
          <div className="mt-4">
            <TempPasswordPanel email={result.email} password={result.temp_password}
                               onClose={onClose} />
          </div>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-4">
            <div>
              <label htmlFor="invite-email" className="block text-sm font-medium">
                이메일
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
                역할
              </label>
              <select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as UserRole)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="pm">PM — 프로젝트 전체 접근</option>
                <option value="admin">관리자 — PM 권한 + 사용자 관리</option>
              </select>
            </div>
            {error && <p className="text-sm text-rose-600">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose}
                      className="rounded-lg border border-slate-300 px-4 py-2 text-sm">
                취소
              </button>
              <button type="submit" disabled={busy}
                      className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                {busy ? "초대 중…" : "초대"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
