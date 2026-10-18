import { describe, it, expect, afterEach, vi } from "vitest";
import { previewUrl } from "./preview";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("previewUrl (deferred build-backend seam)", () => {
  it("returns null when no preview base URL is configured (the state today)", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "");
    expect(previewUrl("ws-m")).toBeNull();
    expect(previewUrl("ws-m", "proto-1")).toBeNull();
  });

  it("builds a preview URL from the configured base when the build backend is present", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com");
    expect(previewUrl("ws-m", "proto-1")).toBe(
      "https://preview.example.com/projects/ws-m/preview/proto-1",
    );
  });

  it("defaults the prototype id to 'default' and strips a trailing slash on the base", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com/");
    expect(previewUrl("ws-m")).toBe(
      "https://preview.example.com/projects/ws-m/preview/default",
    );
  });
});
