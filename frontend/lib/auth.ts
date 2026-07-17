// Auth placeholder seam. The spec defers SSO ("인증: … SSO는 이후 단계"); today
// this returns undefined so no auth header is sent. When session tokens / SSO
// land, return the token here and every client call picks it up automatically —
// no call-site changes.
export function getAuthToken(): string | undefined {
  return undefined;
}
