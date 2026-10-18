import { describe, expect, it, vi, afterEach } from "vitest";
import {
  deleteDesignProfile, getDesignProfile, previewDesignProfile,
  uploadDesignProfile,
} from "./design";

// Declare the arguments with types — with an argument-less vi.fn(), the elements of mock.calls are empty
// tuples, so every line reading calls[0][1] becomes a type error.
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
    // The browser attaches the boundary — if we hard-code Content-Type, parsing breaks.
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("sends the confirmed tokens as a JSON field beside the file", async () => {
    // The server plants this value in the source as a ```tokens block — if the field name or encoding
    // diverges, the value is silently ignored and an unbranded profile is saved.
    const spy = mockFetch({ profile: { filename: "acme.md", uploaded_at: "t",
                                       uploaded_by: "a", tokens: {}, prose: "" } });
    await uploadDesignProfile(
      new File(["# x"], "acme.md", { type: "text/markdown" }),
      { primary: "#00754a" });
    const form = (spy.mock.calls[0][1] as RequestInit).body as FormData;
    expect(JSON.parse(form.get("tokens") as string)).toEqual({ primary: "#00754a" });
  });

  it("omits the tokens field when there is nothing confirmed", async () => {
    // Sending an empty object would leave the server unable to tell "confirmed but empty" from "not confirmed".
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
