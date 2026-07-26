// frontend/lib/auth/sessionRecovery.ts
//
// EventSource는 응답 상태코드를 노출하지 않는다 — 401(토큰 만료)이든 네트워크
// 끊김이든 똑같이 onerror로만 온다. 스트림이 죽은 뒤 세션을 한 번 확인해서
// 만료라면 로그인으로 보낸다. 그러지 않으면 사용자는 "연결이 끊어졌습니다"를
// 반복해서 보며 왜 안 되는지 알 수 없다.
//
// /api 프록시의 리프레시가 스트림에는 적용되지 않는 이유도 같다: SSE는 응답을
// 이미 스트리밍 중이라 401을 받은 시점에 재시도가 무의미하다.

// navigate를 주입받는 이유: 훅에서는 next/navigation의 router.push를 넘기고,
// 테스트에서는 스파이를 넘긴다. 기본값은 전체 페이지 이동 —
// 로그인 왕복은 어차피 앱 상태를 버리므로 클라이언트 라우팅의 이점이 없다.
function defaultNavigate(url: string): void {
  window.location.assign(url);
}

export async function redirectIfSessionExpired(
  navigate: (url: string) => void = defaultNavigate,
  currentPath?: string,
): Promise<boolean> {
  let alive: boolean;
  try {
    const res = await fetch("/api/auth/me", { credentials: "include" });
    alive = res.ok;
  } catch {
    // 확인 자체가 실패했다 — 백엔드가 잠깐 죽은 것뿐일 수 있으므로 사용자를
    // 작업 중인 화면에서 쫓아내지 않는다.
    return false;
  }
  if (alive) return false;
  const next = currentPath
    ? `/login?next=${encodeURIComponent(currentPath)}`
    : "/login";
  navigate(next);
  return true;
}
