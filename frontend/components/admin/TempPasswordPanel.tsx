"use client";
import { useState } from "react";

import { useT } from "@/lib/i18n/provider";

// The server does not store the temporary password — this screen is the only chance to see the
// value. That is why the warning and the copy button sit together.
export function TempPasswordPanel({
  email, password, onClose,
}: {
  email: string;
  password: string;
  onClose: () => void;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      // A browser without clipboard permission — the value is visible on screen, so manual copying is possible.
      setCopied(false);
    }
  }

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <p className="text-sm font-medium text-amber-900">
        <span>{email}</span> {t("admin.tempPasswordOf")}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 rounded bg-white px-3 py-2 font-mono text-sm border border-amber-200">
          {password}
        </code>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg bg-amber-600 px-3 py-2 text-sm text-white hover:bg-amber-700"
        >
          {t("admin.copy")}
        </button>
      </div>
      {copied && <p className="mt-1 text-xs text-amber-700">{t("admin.copied")}</p>}
      <p className="mt-2 text-xs text-amber-800">
        {t("admin.tempPasswordWarnPrefix")} <strong>{t("admin.tempPasswordWarnBold")}</strong>
        {t("admin.tempPasswordWarnSuffix")}
      </p>
      <button
        type="button"
        onClick={onClose}
        className="mt-3 rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-900 hover:bg-amber-100"
      >
        {t("admin.ok")}
      </button>
    </div>
  );
}
