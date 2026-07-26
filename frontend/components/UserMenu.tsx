"use client";
import Link from "next/link";
import { useEffect, useState } from "react";

interface Me {
  authenticated: boolean;
  email?: string | null;
  role?: "admin" | "pm" | null;
}

const ROLE_LABEL: Record<string, string> = { admin: "관리자", pm: "PM" };

// 토큰이 httpOnly 쿠키에 있어 JS가 읽을 수 없으므로, 표시용 정보는 서버에게
// 묻는다(/api/auth/me). 부모가 prop으로 넘기지 않는 이유: 이 컴포넌트가 헤더의
// 모든 화면에 들어가므로 각 페이지가 사용자 정보를 실어 보내는 배선을 만들지 않는다.
export function UserMenu() {
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { authenticated: false }))
      .then((body) => { if (alive) setMe(body); })
      .catch(() => { if (alive) setMe({ authenticated: false }); });
    return () => { alive = false; };
  }, []);

  // 인증 전(로그인 화면)에는 아무것도 그리지 않는다.
  if (!me?.authenticated || !me.email) return null;

  const initial = me.email.charAt(0).toUpperCase();

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`사용자 메뉴 (${me.email})`}
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
              {me.role ? ROLE_LABEL[me.role] : "역할 없음"}
            </p>
          </div>
          {me.role === "admin" && (
            <Link
              href="/admin/users"
              className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
            >
              사용자 관리
            </Link>
          )}
          {/* POST인 이유: GET 로그아웃은 링크 프리페치에 걸려 의도치 않게
              세션을 끊을 수 있다. */}
          <form action="/api/auth/logout" method="post">
            <button
              type="submit"
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-rose-700 hover:bg-rose-50"
            >
              로그아웃
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
