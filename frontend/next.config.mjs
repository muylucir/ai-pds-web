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
};
export default nextConfig;
