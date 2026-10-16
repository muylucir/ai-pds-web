// frontend/components/ProjectMembersModal.tsx — 프로젝트의 소유자와 열람 멤버.
//
// 소유자와 관리자가 연다. 멤버는 **열람만** 한다(backend aipds/project_access.py) — 이 화면에
// "편집 권한" 같은 선택지가 없는 이유다. 관리자에게는 소유권을 다른 사용자에게 주는 칸이
// 하나 더 있다.
//
// 값은 Cognito username(이메일 로컬파트)으로 저장된다. 입력은 이메일로 받고, 서버가 바꾼다.
// 멤버를 추가할 때 계정이 있는지 확인하지 않는다(확인하면 아무 이메일로 계정 존재를 떠볼 수
// 있다) — 그래서 안내 문구가 "그 이메일로 로그인한 사용자에게 보인다"고 말한다.
"use client";
import { useEffect, useState } from "react";

import { assignProjectOwner } from "@/lib/api/adminUsers";
import { ApiError, setProjectMembers } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import type { ProjectSummary } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

export function ProjectMembersModal({
  project,
  onClose,
  onChanged,
}: {
  project: ProjectSummary;
  onClose: () => void;
  /** 저장이 끝났다 — 목록이 소유자 열을 다시 읽는다. */
  onChanged: () => void;
}) {
  const t = useT();
  const isAdmin = project.access === "admin";
  const [owner, setOwner] = useState(project.owner ?? "");
  const [members, setMembers] = useState<string[]>(project.members ?? []);
  const [adding, setAdding] = useState("");
  const [newOwner, setNewOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  const fail = (err: unknown) =>
    setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("members.saveFailed"));

  // 추가·제거는 바로 저장한다. 모아서 저장하는 단계를 두면 창을 닫을 때 바뀌지 않은 것을
  // 바뀐 것으로 오해한다.
  async function saveMembers(next: string[]) {
    setBusy(true);
    setError(null);
    try {
      const r = await setProjectMembers(project.project_id, next);
      setMembers(r.members ?? next);
      onChanged();
      return true;
    } catch (err) {
      fail(err);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const value = adding.trim();
    if (!value) return;
    if (await saveMembers([...members, value])) setAdding("");
  }

  async function changeOwner() {
    const value = newOwner.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const r = await assignProjectOwner(project.project_id, value);
      setOwner(r.owner);
      setMembers(r.members);
      setNewOwner("");
      onChanged();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  const name = project.name ?? project.project_id;
  return (
    <div
      className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
      onClick={() => !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("members.dialogLabel")}
        className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl space-y-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-bold text-lg">{t("members.title").replace("{name}", name)}</h2>

        <div>
          <p className="text-xs font-medium text-slate-500">{t("members.owner")}</p>
          <p className="mt-1 text-sm font-mono text-slate-800" data-testid="members-owner">{owner}</p>
          {isAdmin && (
            <div className="mt-2">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={newOwner}
                  onChange={(e) => setNewOwner(e.target.value)}
                  placeholder={t("members.ownerPlaceholder")}
                  aria-label={t("members.newOwnerLabel")}
                  className="flex-1 min-w-0 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                />
                <button
                  type="button"
                  onClick={() => void changeOwner()}
                  disabled={busy || !newOwner.trim()}
                  className="px-3 py-1.5 text-sm rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {t("members.changeOwner")}
                </button>
              </div>
              <p className="mt-1 text-xs text-slate-400">{t("members.ownerHint")}</p>
            </div>
          )}
        </div>

        <div>
          <p className="text-xs font-medium text-slate-500">{t("members.viewers")}</p>
          <p className="mt-1 text-xs text-slate-400">{t("members.viewersHint")}</p>
          {members.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">{t("members.empty")}</p>
          ) : (
            <ul className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200">
              {members.map((m) => (
                <li key={m} className="flex items-center justify-between px-3 py-1.5 text-sm">
                  <span className="font-mono text-slate-700">{m}</span>
                  <button
                    type="button"
                    aria-label={`${m} ${t("members.removeAria")}`}
                    disabled={busy}
                    onClick={() => void saveMembers(members.filter((x) => x !== m))}
                    className="text-xs text-slate-400 hover:text-rose-600 disabled:opacity-50"
                  >
                    {t("members.remove")}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void add();
            }}
          >
            <input
              type="text"
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              placeholder={t("members.addPlaceholder")}
              aria-label={t("members.addLabel")}
              className="flex-1 min-w-0 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            />
            <button
              type="submit"
              disabled={busy || !adding.trim()}
              className="px-3 py-1.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 text-white font-medium disabled:opacity-50"
            >
              {t("members.add")}
            </button>
          </form>
        </div>

        {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {t("members.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
