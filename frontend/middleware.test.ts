// Contract for the Location header of middleware redirects.
//
// Pins down two bugs observed in a real deployment.
//
// 1) `new URL(path, req.url)` leaks the internal address behind a proxy. When
//    accessed via CloudFront, Location was
//    `https://localhost:3000/login?next=%2F`. This is because Next 15 assembles
//    req.url from its own origin as known to `next start`, not from the Host
//    header (confirmed on EC2: the result was the same whether Host was set to
//    the CloudFront domain or X-Forwarded-Host was set).
//
// 2) That said, a relative Location cannot be used either. **Middleware runs in
//    the Edge runtime, and that runtime internally parses the response's
//    location header with new URL()** — a relative value yields a 500 with
//    `TypeError: Invalid URL, input: '/login?next=%2F'` (observed). A route
//    handler (Node runtime) accepts relative values, but middleware does not.
//    Unit tests did not catch this difference because they only look at the
//    NextResponse object and never go through the runtime's header parsing.
//
// Conclusion: middleware uses an absolute URL but takes the origin from the
// **Host header**, not from req.url. That is the host the user actually
// connected to (nginx forwards it via proxy_set_header Host).
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";

// Behind-a-proxy situation: the request URL's origin is Next's internal
// address, and the host the user actually connected to exists only in the Host
// header.
const INTERNAL = "http://localhost:3000";
const PUBLIC_HOST = "d1wyghhz9isoih.cloudfront.net";

function req(path: string, opts: { token?: string; host?: string | null;
                                   proto?: string } = {}) {
  const headers = new Headers();
  if (opts.host !== null) headers.set("host", opts.host ?? PUBLIC_HOST);
  headers.set("x-forwarded-proto", opts.proto ?? "https");
  const r = new NextRequest(new URL(path, INTERNAL), { method: "GET", headers });
  if (opts.token) r.cookies.set(ACCESS_COOKIE, opts.token);
  return r;
}

// Since the gate does not verify the signature, only the payload shape needs to
// match (see the middleware.ts comment).
function tokenFor(groups: string[]): string {
  const body = Buffer.from(JSON.stringify({ "cognito:groups": groups }))
    .toString("base64url");
  return `x.${body}.y`;
}

describe("middleware의 Location은 사용자가 접속한 호스트를 쓴다", () => {
  it("sends an unauthenticated visitor to /login on the PUBLIC host", () => {
    const location = middleware(req("/")).headers.get("location")!;
    // If the internal address leaks, the user is left unable to connect.
    expect(location).not.toContain("localhost");
    expect(location).toBe(`https://${PUBLIC_HOST}/login?next=%2F`);
  });

  it("emits an ABSOLUTE url — the Edge runtime rejects a relative Location", () => {
    // A relative value makes the runtime throw in new URL(), resulting in a 500
    // (observed deployment error).
    const location = middleware(req("/")).headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
    expect(location).toMatch(/^https:\/\//);
  });

  it("keeps the next param so the user returns to where they were going", () => {
    const location = middleware(req("/projects/p1/dashboard")).headers.get("location")!;
    expect(location)
      .toBe(`https://${PUBLIC_HOST}/login?next=%2Fprojects%2Fp1%2Fdashboard`);
  });

  it("sends a pm who typed an admin URL home on the PUBLIC host", () => {
    const location = middleware(req("/admin/users", { token: tokenFor(["pm"]) }))
      .headers.get("location")!;
    expect(location).not.toContain("localhost");
    expect(location).toBe(`https://${PUBLIC_HOST}/`);
  });

  it("honours the forwarded protocol so local http dev is not forced to https", () => {
    const location = middleware(req("/", { host: "localhost:3000", proto: "http" }))
      .headers.get("location")!;
    expect(location).toBe("http://localhost:3000/login?next=%2F");
  });

  it("falls back to the request origin when there is no Host header", () => {
    // Must work without throwing even for requests without Host (HTTP/1.0,
    // etc.) — if the middleware throws, every page becomes a 500.
    const location = middleware(req("/", { host: null })).headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
    expect(location.endsWith("/login?next=%2F")).toBe(true);
  });

  it("ignores a Host header that would break URL parsing", () => {
    // Host is a value the user can tamper with. An unparsable value falls back
    // to the request origin and never throws.
    const location = middleware(req("/", { host: "bad host\\value" }))
      .headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
  });

  it("lets an allowed request through without a Location", () => {
    const res = middleware(req("/", { token: tokenFor(["admin"]) }));
    expect(res.headers.get("location")).toBeNull();
  });

  it("does not gate the public survey route", () => {
    // Path for end users without accounts — redirecting to login would block
    // them from receiving the questionnaire.
    expect(middleware(req("/survey/tok123")).headers.get("location")).toBeNull();
  });
});
