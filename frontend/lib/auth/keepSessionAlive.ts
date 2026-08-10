// frontend/lib/auth/keepSessionAlive.ts
//
// access 토큰을 주기적으로 갱신한다. 요청이 401로 실패하기를 기다리지 않는
// 것이 핵심이다 — 그 이유는 app/api/auth/refresh/route.ts에 적혀 있다.
//
// 요약: /api 프록시의 갱신은 백엔드 401에만 반응하는데, 프로토타입 빌드는 한
// 번의 SSE 연결로 수십 분을 살면서 그 연결에서 401을 다시 받을 일이 없다.
// 그래서 스트림이 도는 동안 갱신 기회가 하나도 없고, access 토큰(60분)이
// 빌드 도중 만료된다. 짧은 턴이 반복되는 디스커버리 채팅은 매 턴이 갱신
// 기회여서 이 결함이 드러나지 않았다.
//
// sessionRecovery.ts와 역할이 다르다: 그쪽은 스트림이 **죽은 뒤** 만료를
// 확인해 로그인으로 보내는 사후 처리이고, 이쪽은 애초에 만료되지 않게 하는
// 예방이다. 둘 다 필요하다 — 30일 refresh 창이 끝나면 예방은 불가능하고
// 사후 처리만 남는다.

//: 갱신 간격. access 토큰 수명(60분,
//: infra/lib/auth-client-config.ts의 ACCESS_TOKEN_VALIDITY_MINUTES)의 넉넉히
//: 절반 아래로 둔다. 60분에 가깝게 잡으면 갱신 한 번이 지연·실패했을 때 바로
//: 만료 창이 생긴다. 15분이면 한 토큰 수명 안에 3번의 기회가 있으므로 두 번
//: 연속 실패해도 세션이 살아 있다.
export const REFRESH_INTERVAL_MS = 15 * 60_000;

/** 주기 갱신을 시작하고, 멈추는 함수를 돌려준다.
 *
 *  개시 직후에는 갱신하지 않는다 — 이 함수가 불리는 시점(로그인 직후 또는
 *  화면 진입)에는 토큰이 이미 신선하고, 화면마다 Cognito를 때릴 이유가 없다.
 */
export function keepSessionAlive(): () => void {
  const timer = setInterval(() => {
    void (async () => {
      let res: Response;
      try {
        res = await fetch("/api/auth/refresh", { method: "POST" });
      } catch {
        // 네트워크가 잠깐 끊겼다 — 세션 종료가 아니므로 계속 시도한다.
        // 여기서 멈추면 와이파이가 한 번 끊긴 사용자가 빌드 도중 조용히
        // 세션을 잃는다.
        return;
      }
      // 401 = 리프레시 토큰이 만료(30일)·폐기됐다. 갱신할 방법이 없으므로
      // 폴링을 멈춘다 — 계속 두면 매 간격마다 확실히 실패하는 호출을 반복한다.
      //
      // 여기서 /login으로 보내지는 않는다. 이 함수는 사용자가 보고 있지 않을
      // 수도 있는 배경 타이머이고, 작업 중인 화면에서 사용자를 갑자기 쫓아내는
      // 판단은 실제 요청이 실패했을 때 sessionRecovery.redirectIfSessionExpired가
      // 내린다(그쪽은 5xx와 401을 구분하는 규율을 이미 갖고 있다).
      if (res.status === 401) clearInterval(timer);
    })();
  }, REFRESH_INTERVAL_MS);

  return () => clearInterval(timer);
}
