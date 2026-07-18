import { describe, it, expect, afterEach, vi } from "vitest";
import { previewUrl } from "./preview";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("previewUrl (deferred build-backend seam)", () => {
  it("returns null when no preview base URL is configured (the state today)", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "");
    expect(previewUrl("pilot1")).toBeNull();
    expect(previewUrl("pilot1", "proto-1")).toBeNull();
  });

  it("builds a preview URL from the configured base when the build backend is present", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com");
    expect(previewUrl("pilot1", "proto-1")).toBe(
      "https://preview.example.com/projects/pilot1/preview/proto-1",
    );
  });

  it("defaults the prototype id to 'default' and strips a trailing slash on the base", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com/");
    expect(previewUrl("pilot1")).toBe(
      "https://preview.example.com/projects/pilot1/preview/default",
    );
  });
});
