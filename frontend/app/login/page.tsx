"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

// 오류 코드 → 한국어 안내. 알 수 없는 코드는 일반 문구로 떨어진다 — 쿼리
// 파라미터를 그대로 렌더하면 반사형 XSS 표면이 된다.
const MESSAGES: Record<string, string> = {
  state_mismatch: "로그인 요청이 만료되었거나 일치하지 않습니다. 다시 시도해 주세요.",
  exchange_failed: "인증 서버와의 통신에 실패했습니다. 다시 시도해 주세요.",
  access_denied: "로그인이 취소되었습니다.",
  not_configured: "인증이 설정되지 않았습니다. 관리자에게 문의하세요.",
};

const BUTTON_CLASS =
  "mt-6 block w-full rounded-lg bg-violet-600 px-4 py-3 text-center text-sm font-medium text-white hover:bg-violet-700";

/* Link가 아니라 a여야 한다: /api/auth/login은 페이지가 아니라 외부 리다이렉트를
   내는 route handler이고, 클라이언트 라우팅으로는 그 리다이렉트를 따라갈 수 없다. */
function LoginButton({ href }: { href: string }) {
  return (
    <a href={href} className={BUTTON_CLASS}>
      로그인
    </a>
  );
}

// useSearchParams()를 쓰는 부분만 따로 떼어 Suspense 안에 둔다. Next 15는 이
// 페이지를 빌드 시 프리렌더하는데, 그때 훅이 suspend하면서 CSR로 넘어가려 한다 —
// 경계가 없으면 빌드가 "useSearchParams() should be wrapped in a suspense
// boundary"로 실패한다. 경계를 좁게 잡아야 카드 껍데기는 서버 HTML에 그대로
// 남는다.
function LoginActions() {
  const params = useSearchParams();
  const error = params.get("error");
  const next = params.get("next");
  return (
    <>
      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {MESSAGES[error] ?? "로그인에 실패했습니다. 다시 시도해 주세요."}
        </p>
      )}
      <LoginButton
        href={next ? `/api/auth/login?next=${encodeURIComponent(next)}` : "/api/auth/login"}
      />
    </>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm">
        <div className="flex items-center gap-2 text-lg font-bold text-violet-700">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-600 text-sm font-bold text-white">
            AI
          </span>
          Pathfinder
        </div>
        <h1 className="mt-6 text-xl font-bold">로그인</h1>
        <p className="mt-1 text-sm text-slate-500">
          워크숍 계정으로 로그인하세요. 계정이 없으면 관리자에게 초대를 요청하세요.
        </p>

        {/* fallback은 next 없는 기본 로그인 링크다 — 파라미터를 못 읽어도 로그인
            자체는 눌릴 수 있어야 하고, 최악의 경우 원래 가려던 화면 대신 기본
            화면으로 떨어지는 것뿐이다. */}
        <Suspense fallback={<LoginButton href="/api/auth/login" />}>
          <LoginActions />
        </Suspense>
      </div>
    </main>
  );
}
