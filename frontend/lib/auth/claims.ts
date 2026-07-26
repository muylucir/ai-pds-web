// frontend/lib/auth/claims.ts
//
// ⚠️ 여기서 서명을 검증하지 않는다. 이 모듈은 UX용이다:
//   - middleware가 /admin 게이트를 걸 때 (역할 표시/차단)
//   - /api/auth/me가 화면에 보여줄 이메일·역할을 낼 때
//
// 실제 방어선은 백엔드의 JWT 검증이다. 쿠키를 위조한 사용자는 이 파일을 속여
// /admin 화면을 열 수 있지만, 그 화면이 부르는 모든 API가 403으로 막힌다.
// 이 구분을 흐리지 말 것 — 미들웨어를 보안 경계로 착각하는 것이 이 패턴의
// 전형적인 사고다.

export interface Claims {
  [k: string]: unknown;
}

export function decodeJwtPayload(token: string): Claims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = Buffer.from(parts[1], "base64url").toString("utf8");
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? parsed
      : null;
  } catch {
    // 미들웨어가 이걸 부른다 — 예외가 나면 모든 페이지가 500이 된다.
    return null;
  }
}

// 백엔드 verifier(_role_from_groups)와 같은 우선순위여야 한다: 두 그룹에 모두
// 속하면 admin. 어긋나면 화면과 실제 권한이 불일치한다.
export function roleFromClaims(c: Claims | null): "admin" | "pm" | null {
  const groups = c?.["cognito:groups"];
  if (!Array.isArray(groups)) return null;
  const names = groups.map(String);
  if (names.includes("admin")) return "admin";
  if (names.includes("pm")) return "pm";
  return null;
}

export function emailFromClaims(c: Claims | null): string | null {
  // access 토큰에는 email이 없으므로 username으로 떨어진다.
  const email = c?.email ?? c?.username;
  return typeof email === "string" && email ? email : null;
}

export function isExpired(c: Claims | null, nowSeconds: number): boolean {
  const exp = c?.exp;
  // fail-closed: exp를 못 읽으면 만료로 본다(리프레시를 유발할 뿐이다).
  if (typeof exp !== "number") return true;
  return nowSeconds > exp;
}
