// Default "happy" handlers describing the backend contract. Individual tests
// override specific routes with server.use(...); these defaults keep a screen
// test from erroring on an unhandled request it doesn't care about.
import { http, HttpResponse } from "msw";
import { API_BASE_URL } from "@/lib/api/client";

export const handlers = [
  http.get(`${API_BASE_URL}/projects`, () => HttpResponse.json({ projects: [] })),
  http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
    const body = (await request.json()) as { project_id: string; name?: string };
    return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null });
  }),
];
