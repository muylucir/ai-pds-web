// The auth seam. The session is in an httpOnly cookie (aipds_access) that JS cannot read, so
// all the client has to do is tell fetch to include the cookie. The same-origin
// /api proxy translates that cookie into Authorization: Bearer (lib/api/proxyAuth.ts).
//
// Why this constant is not a literal: three client files use the same value, and scattering it
// means missing one place when the policy changes.
export const CREDENTIALS: RequestCredentials = "include";
