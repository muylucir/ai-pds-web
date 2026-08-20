import { describe, expect, it, vi, afterEach } from "vitest";
import {
  deleteDesignProfile, getDesignProfile, previewDesignProfile,
  uploadDesignProfile,
} from "./design";

// 인자를 타입과 함께 선언한다 — 인자 없는 vi.fn()은 mock.calls의 요소가 빈
// 튜플이라 calls[0][1]을 읽는 모든 줄이 타입 오류가 된다.
function mockFetch(body: unknown, status = 200) {
  const spy = vi.fn(async (_url: string, _init?: RequestInit) => new Response(
    status === 204 ? null : JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe("design profile client", () => {
  it("returns null when no profile is set", async () => {
    mockFetch({ profile: null });
    expect(await getDesignProfile()).toBeNull();
  });

  it("returns the profile with its tokens", async () => {
    mockFetch({ profile: { filename: "acme.md", uploaded_at: "t",
                           uploaded_by: "admin@x",
                           tokens: { primary: "#5b2ea6" }, prose: "톤" } });
    const profile = await getDesignProfile();
    expect(profile?.tokens.primary).toBe("#5b2ea6");
  });

  it("uploads multipart WITHOUT forcing a JSON content-type", async () => {
    const spy = mockFetch({ profile: { filename: "acme.md", uploaded_at: "t",
                                       uploaded_by: "a", tokens: {}, prose: "" } });
    await uploadDesignProfile(new File(["# x"], "acme.md", { type: "text/markdown" }));
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    // boundary는 브라우저가 붙인다 — 우리가 Content-Type을 박으면 파싱이 깨진다.
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("sends the confirmed tokens as a JSON field beside the file", async () => {
    // 서버가 이 값을 원문에 ```tokens 블록으로 심는다 — 필드 이름과 인코딩이
    // 어긋나면 값이 조용히 무시되고 무브랜드 프로필이 저장된다.
    const spy = mockFetch({ profile: { filename: "acme.md", uploaded_at: "t",
                                       uploaded_by: "a", tokens: {}, prose: "" } });
    await uploadDesignProfile(
      new File(["# x"], "acme.md", { type: "text/markdown" }),
      { primary: "#00754a" });
    const form = (spy.mock.calls[0][1] as RequestInit).body as FormData;
    expect(JSON.parse(form.get("tokens") as string)).toEqual({ primary: "#00754a" });
  });

  it("omits the tokens field when there is nothing confirmed", async () => {
    // 빈 객체를 보내면 서버가 "확인됐지만 비어 있음"과 "확인 안 함"을 구분할 수 없다.
    const spy = mockFetch({ profile: { filename: "acme.md", uploaded_at: "t",
                                       uploaded_by: "a", tokens: {}, prose: "" } });
    await uploadDesignProfile(
      new File(["# x"], "acme.md", { type: "text/markdown" }), {});
    const form = (spy.mock.calls[0][1] as RequestInit).body as FormData;
    expect(form.get("tokens")).toBeNull();
  });

  it("previews by POSTing to its own route and saves nothing", async () => {
    const spy = mockFetch({ tokens: { primary: "#00754a" }, origin: "extracted",
                            warnings: [] });
    const preview = await previewDesignProfile(
      new File(["# x"], "acme.md", { type: "text/markdown" }));
    expect(preview.origin).toBe("extracted");
    expect(spy.mock.calls[0][0]).toContain("/admin/design/preview");
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("deletes without a body", async () => {
    const spy = mockFetch(null, 204);
    await deleteDesignProfile();
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
