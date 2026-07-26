// frontend/lib/auth/safeNext.ts
//
// 로그인 후 돌아갈 경로의 유일한 검증 지점. login과 callback 양쪽에서 쓴다 —
// 두 곳에 복붙된 검증은 한쪽만 고쳐지는 검증이 된다.
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
  return `${candidate.pathname}${candidate.search}${candidate.hash}`;
}
