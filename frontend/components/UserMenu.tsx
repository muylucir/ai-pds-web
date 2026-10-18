"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
// Imported as a namespace — the test observes the timer start with vi.spyOn
// (a named import's binding is fixed, so the spy would not attach).
import * as keepAlive from "@/lib/auth/keepSessionAlive";
import { ChangePasswordModal } from "./ChangePasswordModal";

interface Me {
  authenticated: boolean;
  email?: string | null;
  role?: "admin" | "pm" | null;
}

// Same convention as the admin screen (UserTable) — labels are dictionary keys.
const ROLE_LABEL_KEY: Record<string, keyof Dict> = {
  admin: "admin.roleAdmin",
  pm: "admin.rolePm",
};

// The token is in an httpOnly cookie that JS cannot read, so display info is
// fetched from the server (/api/auth/me). Why the parent does not pass it as a
// prop: this component sits in the header of every screen, so we avoid wiring
// every page to pass user info down.
export function UserMenu() {
  const t = useT();
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);
  // Close the menu when opening the modal — if the open menu stayed behind the
  // modal, outside-click handling would be layered twice.
  const [changing, setChanging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Periodic refresh of the access token. It lives in this component for the same
  // reason /api/auth/me is called here — it sits in the header of every
  // authenticated screen, so the wiring is not repeated per page.
  //
  // Why not wrap only the prototype screens: long Discovery turns in the workspace
  // have the same expiry window (see app/api/auth/refresh/route.ts — refresh reacts
  // only to a backend 401, so an open SSE connection creates no refresh opportunity).
  useEffect(() => keepAlive.keepSessionAlive(), []);

  useEffect(() => {
    let alive = true;
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { authenticated: false }))
      .then((body) => { if (alive) setMe(body); })
      .catch(() => { if (alive) setMe({ authenticated: false }); });
    return () => { alive = false; };
  }, []);

  // Attach listeners only while open — so no listener remains on document while
  // closed. Why mousedown: with click, the very click that opens the menu could
  // bubble up to document and close it immediately on the same click. The ref
  // excludes clicks inside the menu (toggle button, items) from "outside clicks".
  useEffect(() => {
    if (!open) return;
    function handlePointerDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  // Render nothing before authentication (the login screen).
  if (!me?.authenticated || !me.email) return null;

  const initial = me.email.charAt(0).toUpperCase();

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("user.menuAria").replace("{email}", me.email)}
        aria-expanded={open}
        className="h-9 w-9 rounded-full bg-violet-100 text-sm font-bold text-violet-700"
      >
        {initial}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <div className="px-3 py-2">
            <p className="truncate text-sm font-medium">{me.email}</p>
            <p className="text-xs text-slate-500">
              {ROLE_LABEL_KEY[me.role ?? ""] ? t(ROLE_LABEL_KEY[me.role ?? ""]) : t("user.noRole")}
            </p>
          </div>
          {me.role === "admin" && (
            <>
              <Link
                href="/admin/users"
                className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              >
                {t("user.manageUsers")}
              </Link>
              <Link
                href="/admin/models"
                className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              >
                {t("user.manageModels")}
              </Link>
              <Link
                href="/admin/design"
                className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              >
                {t("user.manageDesign")}
              </Link>
            </>
          )}
          {/* Kept outside the role condition — it acts on one's own account, so a pm
              must be able to do it too. Inside the admin block, a pm would be left
              only with asking an admin for a reset. */}
          <button
            type="button"
            onClick={() => { setOpen(false); setChanging(true); }}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50"
          >
            {t("user.changePassword")}
          </button>
          {/* Why POST: a GET logout can be triggered by link prefetching and end
              the session unintentionally. */}
          <form action="/api/auth/logout" method="post">
            <button
              type="submit"
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-rose-700 hover:bg-rose-50"
            >
              {t("user.signOut")}
            </button>
          </form>
        </div>
      )}
      {changing && <ChangePasswordModal onClose={() => setChanging(false)} />}
    </div>
  );
}
