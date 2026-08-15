"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
// 네임스페이스로 가져온다 — 테스트가 vi.spyOn으로 타이머 시작을 관찰한다
// (명명 import는 바인딩이 고정되어 스파이가 걸리지 않는다).
import * as keepAlive from "@/lib/auth/keepSessionAlive";

interface Me {
  authenticated: boolean;
  email?: string | null;
  role?: "admin" | "pm" | null;
}

// admin 화면(UserTable)과 같은 규약 — 라벨은 딕셔너리 키다.
const ROLE_LABEL_KEY: Record<string, keyof Dict> = {
  admin: "admin.roleAdmin",
  pm: "admin.rolePm",
};

// 토큰이 httpOnly 쿠키에 있어 JS가 읽을 수 없으므로, 표시용 정보는 서버에게
// 묻는다(/api/auth/me). 부모가 prop으로 넘기지 않는 이유: 이 컴포넌트가 헤더의
// 모든 화면에 들어가므로 각 페이지가 사용자 정보를 실어 보내는 배선을 만들지 않는다.
export function UserMenu() {
  const t = useT();
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // access 토큰의 주기 갱신. 이 컴포넌트에 있는 이유는 /api/auth/me를 여기서
  // 부르는 것과 같다 — 인증된 모든 화면의 헤더에 들어가므로 페이지마다 배선을
  // 반복하지 않는다.
  //
  // 프로토타입 화면만 감싸지 않는 이유: 워크스페이스의 긴 디스커버리 턴도 같은
  // 만료 창을 갖는다(app/api/auth/refresh/route.ts 참조 — 갱신이 백엔드 401에만
  // 반응하므로 열려 있는 SSE 연결은 갱신 기회를 만들지 못한다).
  useEffect(() => keepAlive.keepSessionAlive(), []);

  useEffect(() => {
    let alive = true;
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { authenticated: false }))
      .then((body) => { if (alive) setMe(body); })
      .catch(() => { if (alive) setMe({ authenticated: false }); });
    return () => { alive = false; };
  }, []);

  // 열려 있는 동안에만 리스너를 붙인다 — 닫혀 있을 때 document에 리스너가
  // 남아있지 않게 한다. mousedown을 쓰는 이유: click을 쓰면 메뉴를 여는 그
  // 클릭이 버블링되어 document까지 올라가 같은 클릭에 바로 닫힐 위험이 있다.
  // ref로 메뉴 내부 클릭(토글 버튼·항목)은 "바깥 클릭"에서 제외한다.
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

  // 인증 전(로그인 화면)에는 아무것도 그리지 않는다.
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
          {/* POST인 이유: GET 로그아웃은 링크 프리페치에 걸려 의도치 않게
              세션을 끊을 수 있다. */}
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
    </div>
  );
}
