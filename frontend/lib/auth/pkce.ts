// frontend/lib/auth/pkce.ts
//
// PKCE(RFC 7636). Web Crypto만 쓴다 — Node 20+와 Next의 edge/node 런타임
// 양쪽에서 동작해야 하므로 node:crypto를 쓰지 않는다.

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// code_verifier와 state 양쪽에 쓴다. 43자 이상이어야 한다(RFC 7636 §4.1).
export function randomUrlSafe(bytes = 32): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return base64url(buf);
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}
