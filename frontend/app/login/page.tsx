"use client";
import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// Error code → dictionary key. Unknown codes fall back to the generic wording — rendering the query
// parameter as is would create a reflected XSS surface. The values are keyof Dict, so
// typos become compile errors.
const MESSAGE_KEYS: Record<string, keyof Dict> = {
  state_mismatch: "login.stateMismatch",
  exchange_failed: "login.exchangeFailed",
  access_denied: "login.accessDenied",
  not_configured: "login.notConfigured",
};

const BUTTON_CLASS =
  "mt-6 block w-full rounded-lg bg-violet-600 px-4 py-3 text-center text-sm font-medium text-white hover:bg-violet-700";

/* It must be an a, not a Link: /api/auth/login is not a page but a route handler that issues an
   external redirect, and client-side routing cannot follow that redirect. */
function LoginButton({ href }: { href: string }) {
  const t = useT();
  return (
    <a href={href} className={BUTTON_CLASS}>
      {t("login.signIn")}
    </a>
  );
}

// Only the part that uses useSearchParams() is split out and placed inside Suspense. Next 15
// prerenders this page at build time, and the hook then suspends and tries to bail out to CSR —
// without a boundary, the build fails with "useSearchParams() should be wrapped in a suspense
// boundary". Keeping the boundary narrow leaves the card shell in the server HTML
// as is.
function LoginActions() {
  const t = useT();
  const params = useSearchParams();
  const error = params.get("error");
  const next = params.get("next");
  return (
    <>
      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {MESSAGE_KEYS[error] ? t(MESSAGE_KEYS[error]) : t("login.generic")}
        </p>
      )}
      <LoginButton
        href={next ? `/api/auth/login?next=${encodeURIComponent(next)}` : "/api/auth/login"}
      />
    </>
  );
}

export default function LoginPage() {
  const t = useT();
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-lg font-bold text-violet-700">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-600 text-sm font-bold text-white">
              WEB
            </span>
            AI-PDS
          </div>
          {/* The way to pick a language before logging in. The chosen value is kept in a cookie and carries over
              to the screens after login (LanguageSwitcher). */}
          <LanguageSwitcher alwaysVisible />
        </div>
        <h1 className="mt-6 text-xl font-bold">{t("login.signIn")}</h1>
        <p className="mt-1 text-sm text-slate-500">
          {t("login.subtitle")}
        </p>

        {/* The fallback is the default login link without next — even if the parameter cannot be read, login
            itself must be clickable, and the worst case is just landing on the default screen instead of the
            one originally intended. */}
        <Suspense fallback={<LoginButton href="/api/auth/login" />}>
          <LoginActions />
        </Suspense>

        {/* A link that must be readable even before logging in. It is the only way for someone waiting for
            an account to find out what this tool is, and why lib/auth/gate.ts
            treats /manual as a public path. */}
        <Link
          href="/manual"
          className="mt-4 block text-center text-sm text-violet-700 hover:underline"
        >
          {t("manual.readManual")}
        </Link>
      </div>
    </main>
  );
}
