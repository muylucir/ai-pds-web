// 프로토타입 빌드 중 세션이 만료되는 결함의 수정.
//
// **왜 이 라우트가 필요한가.** 토큰 갱신은 /api 프록시가 백엔드에서 **401을
// 받았을 때만** 발동한다(app/api/[...path]/route.ts의 isRetryableWithRefresh).
// 프로토타입 빌드는 그 조건에 걸리지 않는다:
//
//   1. POST /turns        — 유효한 토큰, 200
//   2. GET  /events?turn= — 유효한 토큰, 200. SSE 연결 확립
//   3. 이후 수십 분간 이벤트만 흐른다. 이미 200을 받은 연결이라 401이 다시
//      올 일이 없다 — 갱신 경로에 진입할 방법이 아예 없다
//   4. access 토큰(60분)이 그 사이에 만료되고, 빌드가 끝난 뒤 첫 요청에서
//      401 → /login
//
// 즉 스트림이 끊겨서가 아니라 **스트림이 도는 동안 갱신 기회가 없어서** 만료된다.
// 디스커버리 채팅에서 덜 보이는 이유는 턴이 짧아 매 턴이 갱신 기회가 되기
// 때문이다.
//
// 그래서 갱신을 요청 실패에 의존하지 않는 **명시적 경로**로 분리한다. 스트림과
// 무관하게 클라이언트가 주기적으로 호출한다(lib/auth/keepSessionAlive.ts).
//
// /api/auth/me로 대신할 수 없다: 그 라우트는 쿠키 **존재**만 확인하고 갱신하지
// 않으며, /api 프록시를 타지도 않는다.
import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
  vi.resetModules();
});

function request(cookie?: string): Request {
  return new Request("https://app.example.com/api/auth/refresh", {
    method: "POST",
    headers: cookie ? { cookie } : {},
  });
}

/** Cognito /oauth2/token의 성공 응답을 흉내내는 fetch 스텁. */
function stubTokenEndpoint(body: Record<string, unknown>, status = 200) {
  // 인수를 명시적으로 받는다 — 인수 없는 () => ... 로 두면 mock.calls의 튜플
  // 타입이 빈 배열로 추론되어 calls[0][1].body를 읽는 단정이 타입 오류가 된다.
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(JSON.stringify(body), {
      status, headers: { "content-type": "application/json" },
    }));
}

describe("POST /api/auth/refresh", () => {
  it("exchanges the refresh cookie for a new access token and sets it", async () => {
    const fetchSpy = stubTokenEndpoint({
      access_token: "new-access", id_token: "new-id", expires_in: 3600,
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    const res = await POST(request("pf_refresh=r-1") as never);

    expect(res.status).toBe(200);
    const setCookie = res.headers.getSetCookie().join("\n");
    expect(setCookie).toContain("pf_access=new-access");
    expect(setCookie).toContain("pf_id=new-id");
    // httpOnly가 빠지면 갱신된 토큰만 JS로 읽히게 되어, 이 라우트가 XSS
    // 노출면을 새로 만드는 셈이 된다.
    expect(setCookie).toContain("HttpOnly");
  });

  it("uses the refresh_token grant with the cookie's token", async () => {
    const fetchSpy = stubTokenEndpoint({
      access_token: "a", id_token: "i", expires_in: 3600,
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    await POST(request("pf_refresh=r-1") as never);

    const body = String(fetchSpy.mock.calls[0]?.[1]?.body ?? "");
    expect(body).toContain("grant_type=refresh_token");
    expect(body).toContain("refresh_token=r-1");
  });

  it("401s without calling Cognito when there is no refresh cookie", async () => {
    const fetchSpy = stubTokenEndpoint({});
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    const res = await POST(request() as never);

    expect(res.status).toBe(401);
    // 리프레시 토큰이 없으면 호출할 것이 없다 — Cognito를 때리지 않는다.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("401s when the refresh token has expired or been revoked", async () => {
    // 30일이 지났거나 폐기됐다 — 여기서 200을 내면 클라이언트가 살아 있다고
    // 오판해 계속 폴링하고, 사용자는 다음 실제 요청에서야 로그아웃된다.
    vi.stubGlobal("fetch", stubTokenEndpoint({ error: "invalid_grant" }, 400));

    const { POST } = await import("./route");
    const res = await POST(request("pf_refresh=stale") as never);

    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toMatchObject({ authenticated: false });
  });

  it("does not clear the refresh cookie on a successful refresh", async () => {
    // 이 풀은 리프레시 로테이션이 꺼져 있어(infra/lib/auth-client-config.ts의
    // refreshTokenRotationGracePeriod 미지정) refresh 그랜트가 새 refresh_token을
    // 주지 않는다. 기존 쿠키를 건드리면 30일 창이 통째로 사라진다.
    vi.stubGlobal("fetch", stubTokenEndpoint({
      access_token: "a", id_token: "i", expires_in: 3600,
    }));

    const { POST } = await import("./route");
    const res = await POST(request("pf_refresh=r-1") as never);

    const setCookie = res.headers.getSetCookie().join("\n");
    expect(setCookie).not.toContain("pf_refresh=");
  });
});
