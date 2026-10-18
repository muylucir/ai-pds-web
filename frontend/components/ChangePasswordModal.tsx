"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { changePassword } from "@/lib/api/account";
import { useT } from "@/lib/i18n/provider";

// Changing one's own password. For both the seed account and invited accounts, the Hosted UI has
// the user set a password on first login, but after that the Hosted UI has no place to change it
// themselves — Cognito managed login has no 'change password' page, and self-service reset
// (forgot password) cannot be used because this app sends no email. Hence this screen.
//
// Policy checks are not done here: Cognito is the sole judge, two copies of a policy
// inevitably drift, and the drifted one lies to the user. The only check the screen does is
// that the confirmation input matches, and that is not policy but **typo prevention** — if a
// typo were sent to the server, Cognito would make that value the real password, and the user
// would be locked out without knowing what they typed.
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
      // Close only after the server confirms — closing first would make the user believe an
      // unchanged password was changed.
      onClose();
    } catch (err) {
      // Do not clear the input even on failure. Making the user refill all three fields leads
      // them to repeat the same typo.
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
          {/* role="alert", so screen readers read it immediately, and the tests also look at this
              area rather than at static guidance text. */}
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
