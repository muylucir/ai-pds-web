// frontend/lib/api/design.ts — brand design profile (admin only).
//
// Parsing lives in one place only, the backend — we don't pre-validate here. Two parsers
// drift apart, and if the drifted one is the screen, the admin thinks it passed and moves on.
import { apiFetch } from "./http";

export interface DesignProfile {
  filename: string;
  uploaded_at: string;
  uploaded_by: string;
  tokens: Record<string, string>;
  prose: string;
  //: Not stored — the backend derives it from the profile (`no-tokens`). So the upload
  //  response and the response on reopening say the same thing.
  //
  //  It is optional because of deploy order: in the window where the frontend is updated first
  //  and the backend is still the old version, this field arrives missing. Making it required and calling `.includes()`
  //  would take down the whole admin page in that window (there is no error boundary).
  warnings?: string[];
}

//: Result of asking only "which tokens come out of this document" before saving.
//  `origin`: whether the document already had a fence (`fence`), the model extracted them from prose
//  (`extracted`), or nothing was found (`none`).
export interface DesignPreview {
  tokens: Record<string, string>;
  origin: "fence" | "extracted" | "none";
  warnings: string[];
}

//: Downloads open via an anchor — the same-origin proxy swaps the cookie for a Bearer token.
export const DESIGN_RAW_PATH = "/api/admin/design/raw";
export const DESIGN_TEMPLATE_PATH = "/api/admin/design/template";

export async function getDesignProfile(): Promise<DesignProfile | null> {
  const body = await apiFetch<{ profile: DesignProfile | null }>("/admin/design");
  return body?.profile ?? null;
}

export async function previewDesignProfile(file: File): Promise<DesignPreview> {
  const form = new FormData();
  form.append("file", file);
  const body = await apiFetch<DesignPreview>("/admin/design/preview", {
    method: "POST",
    body: form,
  });
  return body as DesignPreview;
}

export async function uploadDesignProfile(
  file: File, tokens?: Record<string, string>,
): Promise<DesignProfile> {
  const form = new FormData();
  form.append("file", file);
  // Send only the confirmed tokens. The server embeds these values into the original as a ```tokens block
  // and saves it (no separately stored derived values), so the next time the original is downloaded these
  // values are in the file. If the document already has a fence, the server ignores this field — the fence is authoritative.
  if (tokens && Object.keys(tokens).length > 0) {
    form.append("tokens", JSON.stringify(tokens));
  }
  const body = await apiFetch<{ profile: DesignProfile }>("/admin/design", {
    method: "PUT",
    body: form,
  });
  return (body as { profile: DesignProfile }).profile;
}

export async function deleteDesignProfile(): Promise<void> {
  await apiFetch<null>("/admin/design", { method: "DELETE" });
}
