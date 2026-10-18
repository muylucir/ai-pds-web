// Default "happy" handlers describing the backend contract. Individual tests
// override specific routes with server.use(...); these defaults keep a screen
// test from erroring on an unhandled request it doesn't care about.
import { http, HttpResponse } from "msw";
import { API_BASE_URL } from "@/lib/api/client";

export const handlers = [
  http.get(`${API_BASE_URL}/projects`, () =>
    HttpResponse.json({ projects: [], total: 0, page: 1, size: 10 })),
  http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
    const body = (await request.json()) as { project_id: string; name?: string };
    return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null });
  }),
  http.get(`${API_BASE_URL}/projects/:pid/artifacts`, () => HttpResponse.json({ artifacts: [] })),
  http.get(`${API_BASE_URL}/projects/:pid/artifacts/missing-references`, () =>
    HttpResponse.json({ document: null, missing: [] })),
  // Called by the document review screen for the approval gate judgment. The default is "not yet
  // approved" (empty history) — in that state, the gate showing is normal.
  http.get(`${API_BASE_URL}/projects/:pid/approvals`, () =>
    HttpResponse.json({ approvals: [], current_doc_hash: null })),
  // Called on every screen by the project creation screen and the header badge — a default is set
  // so screen tests do not have to care about the model list.
  http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
  http.get(`${API_BASE_URL}/projects/:pid`, ({ params }) =>
    HttpResponse.json({ project_id: params.pid, name: null, created_at: null,
                        model_id: null })),
  // UserMenu calls this on every screen — the default is "unauthenticated" so screen tests do not
  // have to care about the user menu.
  http.get("*/api/auth/me", () => HttpResponse.json({ authenticated: false },
                                                    { status: 401 })),
];
