"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { changePassword } from "@/lib/api/account";
import { useT } from "@/lib/i18n/provider";

// 자기 비밀번호 변경. 시드 계정과 초대 계정 모두 첫 로그인에서 Hosted UI가
// 비밀번호를 정하게 하지만, 그 이후에 스스로 바꿀 창구는 Hosted UI에 없다 —
// Cognito managed login에는 '비밀번호 변경' 페이지가 없고, 자가 재설정
// (forgot password)은 이 앱이 메일을 보내지 않아 쓸 수 없다. 그래서 이 화면이다.
//
// 정책 검사를 여기서 하지 않는다: 판정자는 Cognito 하나이고, 두 벌의 정책은
// 반드시 어긋나며 어긋난 쪽이 사용자에게 거짓말을 한다. 화면이 하는 검사는
// 확인 입력 일치 하나뿐인데, 그것은 정책이 아니라 **오타 방지**다 — 오타를
// 서버로 보내면 Cognito가 그 값을 실제 비밀번호로 만들고, 사용자는 자기가
// 무엇을 입력했는지 모르는 상태로 잠긴다.
export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (next !== confirm) {
      setError(t("account.passwordMismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      // 서버가 확인한 뒤에만 닫는다 — 먼저 닫으면 사용자는 바뀌지 않은
      // 비밀번호를 바뀐 것으로 믿는다.
      onClose();
    } catch (err) {
      // 실패해도 입력을 지우지 않는다. 세 칸을 다시 채우게 만들면 사용자는
      // 같은 오타를 반복한다.
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(false);
    }
  }

  const fieldClass = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
        <h2 className="text-lg font-bold">{t("account.changePasswordTitle")}</h2>
        <form onSubmit={submit} className="mt-4 space-y-4">
          <div>
            <label className="block text-sm font-medium" htmlFor="current-password">
              {t("account.currentPassword")}
            </label>
            <input
              id="current-password"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className={fieldClass}
            />
          </div>
          <div>
            <label className="block text-sm font-medium" htmlFor="new-password">
              {t("account.newPassword")}
            </label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              className={fieldClass}
            />
            <p className="mt-1 text-xs text-slate-500">{t("account.passwordHint")}</p>
          </div>
          <div>
            <label className="block text-sm font-medium" htmlFor="confirm-password">
              {t("account.confirmPassword")}
            </label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className={fieldClass}
            />
          </div>
          {/* role="alert"이라 스크린리더가 즉시 읽고, 테스트도 정적 안내 문구가
              아니라 이 영역을 본다. */}
          {error && (
            <p role="alert" className="text-sm text-rose-700">{error}</p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
            >
              {t("admin.commonCancel")}
            </button>
            <button
              type="submit"
              disabled={busy || !current || !next || !confirm}
              className="rounded-lg bg-violet-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? t("account.changingPassword") : t("account.submitPassword")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
