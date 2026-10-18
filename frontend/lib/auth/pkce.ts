// frontend/lib/auth/pkce.ts
//
// PKCE (RFC 7636). Uses only Web Crypto — it must work on Node 20+ and in both Next's edge/node runtimes,
// so node:crypto is not used.

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Used for both code_verifier and state. Must be at least 43 characters (RFC 7636 §4.1).
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
