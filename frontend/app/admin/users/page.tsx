"use client";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { UserTable } from "@/components/admin/UserTable";
import { InviteUserModal } from "@/components/admin/InviteUserModal";
import { ApiError } from "@/lib/api/client";
import { listUsers, type AdminUser } from "@/lib/api/adminUsers";
import { useT } from "@/lib/i18n/provider";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  const [me, setMe] = useState<string | null>(null);

  const t = useT();
  const reload = useCallback(async () => {
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      // A 403 means a pm typed the URL directly — the middleware is only a UX gate, and
      // the actual block shows up here (in the backend response).
      setError(err instanceof ApiError && err.status === 403
        ? t("admin.needAdmin")
        : t("admin.usersLoadFailed"));
      setUsers([]);
    }
  }, [t]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    // The email used to mark the user's own row. Failure is harmless (the marker is just omitted).
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => setMe(body?.email ?? null))
      .catch(() => setMe(null));
  }, []);

  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold">{t("admin.usersTitle")}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {t("admin.usersSubtitle")}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setInviting(true)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700"
          >
            {t("admin.inviteUserTitle")}
          </button>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </p>
        )}
        {users === null && <p className="text-sm text-slate-400">{t("admin.commonLoading")}</p>}
        {users !== null && users.length > 0 && (
          <UserTable users={users} currentEmail={me} onChanged={reload} />
        )}
        {users !== null && users.length === 0 && !error && (
          <p className="text-sm text-slate-500">{t("admin.noUsers")}</p>
        )}

        {inviting && (
          <InviteUserModal
            onInvited={reload}
            onClose={() => setInviting(false)}
          />
        )}
      </main>
    </>
  );
}
