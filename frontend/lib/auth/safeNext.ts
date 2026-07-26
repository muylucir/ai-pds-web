// frontend/lib/auth/safeNext.ts
//
// 로그인 후 돌아갈 경로의 유일한 검증 지점. login과 callback 양쪽에서 쓴다 —
// 두 곳에 복붙된 검증은 한쪽만 고쳐지는 검증이 된다.
//
// 후조건(postcondition) — 호출자가 지켜야 할 게 아니라 이 함수가 보장한다:
//   반환값은 항상 `new URL(반환값, requestUrl)`에 다시 넣어도 안전하고, 그
//   결과는 항상 우리 자신의 origin으로 resolve된다. 이 보장이 깨지면 반환값을
//   다시 resolve하는 어떤 호출자도(지금은 없지만 나중에 생길 수 있다) 오픈
//   리다이렉트에 노출된다.
//
// ⚠️ 문자열 프리픽스 검사("//"로 시작하는지만 본다)는 이 방어를 완전히 뚫린다:
// "/\evil.example"(슬래시 하나 + 백슬래시)는 "//"로 시작하지 않지만, WHATWG
// URL 파서는 특수 스킴에서 백슬래시를 슬래시처럼 취급해 https://evil.example/
// 로 resolve한다. 그래서 여기서는 "raw가 /로 시작하는가"를 미리 걷어내는 데만
// 쓰고, 최종 판단은 항상 실제 URL 파서로 resolve한 뒤 origin을 비교해서
// 내린다 — 새로운 인코딩 트릭이 나와도 origin 비교는 우회되지 않는다.
export function safeNext(raw: string | null | undefined,
                         requestUrl: string): string {
  if (!raw) return "/";
  // 백슬래시는 우리가 생성하는 경로에 절대 나타나지 않는다. resolve 후
  // same-origin으로 떨어지는 경우까지 포함해 통째로 거부한다 — 파서의 백슬래시
  // 처리에 기대지 않는, 예측 가능한 결정이다.
  if (raw.includes("\\")) return "/";

  let candidate: URL;
  let origin: URL;
  try {
    origin = new URL(requestUrl);
    candidate = new URL(raw, origin);
  } catch {
    return "/";
  }
  // 진짜 방어선: 문자열 모양이 아니라 resolve된 origin이 우리 자신인가.
  if (candidate.origin !== origin.origin) return "/";

  const result = `${candidate.pathname}${candidate.search}${candidate.hash}`;
  // same-origin 검사를 통과했어도 raw가 우리 자신의 origin을 절대 URL로 명시한
  // 경우(예: "https://app.example.com//evil.example") pathname 자체가 "//"로
  // 시작할 수 있다. 이 문자열을 나중에 다시 `new URL(result, someUrl)`에
  // 넣으면 protocol-relative로 재해석돼 오프사이트로 튄다 — 지금 호출자는
  // 둘 다 이렇게 재사용하지 않아 발화하지 않지만, 후조건을 무조건 지키려면
  // 여기서도 걷어내야 한다.
  if (result.startsWith("//")) return "/";
  return result;
}
