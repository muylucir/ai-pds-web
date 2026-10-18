import { describe, expect, it } from "vitest";
import * as authModule from "./auth";
import { CREDENTIALS } from "./auth";

describe("auth seam", () => {
  it("exposes credentials:include for cookie-based auth", () => {
    // The token is in an httpOnly cookie and JS cannot read it. All the client has to do is
    // tell fetch to include cookies in the request.
    expect(CREDENTIALS).toBe("include");
  });

  it("no longer exposes getAuthToken", () => {
    // If this function remained, new callers would try to attach an undefined token to the header.
    expect("getAuthToken" in authModule).toBe(false);
  });
});
