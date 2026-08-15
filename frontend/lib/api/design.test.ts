import { describe, expect, it, vi, afterEach } from "vitest";
import { deleteDesignProfile, getDesignProfile, uploadDesignProfile } from "./design";

function mockFetch(body: unknown, status = 200) {
  const spy = vi.fn(async () => new Response(
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

  it("deletes without a body", async () => {
    const spy = mockFetch(null, 204);
    await deleteDesignProfile();
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
