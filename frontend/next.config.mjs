/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Dev is accessed through a proxied hostname (not bare localhost), so Next's
  // dev server flags cross-origin /_next/* requests. Allow the proxy origin.
  allowedDevOrigins: ["frontend.workloom.net"],
  // Same-origin API access: the browser is remote (behind the proxy) and
  // cannot reach the backend's localhost:8000 directly. The client calls
  // same-origin /api/* (NEXT_PUBLIC_API_BASE_URL=/api); a route handler at
  // app/api/[...path]/route.ts proxies to the backend server-side.
  //
  // NOTE: we do NOT use next.config `rewrites()` for this — its node-http-proxy
  // passes the backend's hop-by-hop `Connection: keep-alive` header through and
  // appends `close`, which is illegal over HTTP/2 and breaks SSE in the browser
  // (ERR_HTTP2_PROTOCOL_ERROR). The route handler re-streams with clean headers.
  //
  // Keep trailing slashes intact. By default Next 308-redirects "/a/b/" to
  // "/a/b", which silently breaks proxied prototype previews: their HTML uses
  // RELATIVE asset refs (href="styles.css"), so serving the directory index at
  // the slash-less URL makes the browser resolve assets one level too high
  // (/api/proto/{pid}/styles.css — slug lost → 502). The prototype's own
  // server is what should decide about trailing slashes, not this proxy.
  skipTrailingSlashRedirect: true,
  experimental: {
    // middleware.ts가 /api/*에도 걸리므로 요청 바디는 미들웨어를 한 번 지난다. Next는
    // 이 값을 넘는 바디를 **거부하지 않고 잘라서** 넘긴다(기본 10MB) — 첨부 업로드
    // 상한(10MB, lib/uploads.ts의 MAX_UPLOAD_BYTES)에 multipart 머리를 더하면 넘으므로,
    // 그대로 두면 큰 파일이 깨진 채 백엔드에 도착한다. nginx의 상한(11m)과 같게 둔다.
    middlewareClientMaxBodySize: "11mb",
  },
};
export default nextConfig;
