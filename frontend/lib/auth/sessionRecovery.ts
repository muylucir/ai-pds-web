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

// 동시성 노트: 여러 SSE 훅이 같은 순간에 끊기면 각자 이 함수를 독립적으로
// 호출해 각자 navigate를 부를 수 있다 — 인플라이트 가드를 두지 않는다.
// window.location.assign(같은 URL)로 수렴하는 한 무해하다: history.pushState처럼
// 쌓이는 게 아니라 같은 목적지로의 페이지 이동이 중복될 뿐이다.
export async function redirectIfSessionExpired(
  navigate: (url: string) => void = defaultNavigate,
  currentPath?: string,
): Promise<boolean> {
  let res: Response;
  try {
    res = await fetch("/api/auth/me", { credentials: "include" });
  } catch {
    // 확인 자체가 실패했다 — 백엔드가 잠깐 죽은 것뿐일 수 있으므로 사용자를
    // 작업 중인 화면에서 쫓아내지 않는다.
    return false;
  }
  if (res.ok) return false; // 200 — 세션이 살아 있다.

  // 401만 "세션 없음"의 신뢰할 수 있는 신호로 본다 — 이 엔드포인트
  // (app/api/auth/me/route.ts)가 실제로 내는 실패 상태는 401뿐이다. 5xx는
  // 백엔드가 잠깐 죽었을 뿐 세션과 무관할 수 있으므로 만료로 단정하지
  // 않는다 — 그러지 않으면 배포/재시작 중인 백엔드가 정상 세션의 사용자를
  // 작업 중인 화면에서 쫓아내는 꼴이 된다.
  if (res.status !== 401) return false;

  // 상태코드 하나만으로 단정하지 않고 본문의 authenticated:false로
  // 재확인한다. 본문이 깨져 있으면(파싱 실패) "판정 불가"이지 "만료
  // 확정"이 아니다 — 파싱 실패를 authenticated:false로 잘못 읽으면
  // 판정 불가 상황도 이동시켜 버린다.
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return false;
  }
  const expired =
    typeof body === "object" && body !== null &&
    (body as { authenticated?: unknown }).authenticated === false;
  if (!expired) return false;

  const next = currentPath
    ? `/login?next=${encodeURIComponent(currentPath)}`
    : "/login";
  navigate(next);
  return true;
}
