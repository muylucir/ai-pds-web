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
  // 문서 리뷰 화면이 승인 게이트 판정을 위해 부른다. 기본은 "아직 승인 안 함"
  // (빈 이력) — 그 상태에서 게이트가 떠 있는 것이 정상이다.
  http.get(`${API_BASE_URL}/projects/:pid/approvals`, () =>
    HttpResponse.json({ approvals: [], current_doc_hash: null })),
  // 프로젝트 생성 화면과 헤더 배지가 모든 화면에서 부른다 — 기본을 두어
  // 화면 테스트가 모델 목록을 신경쓰지 않게 한다.
  http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
  http.get(`${API_BASE_URL}/projects/:pid`, ({ params }) =>
    HttpResponse.json({ project_id: params.pid, name: null, created_at: null,
                        model_id: null })),
  // UserMenu가 모든 화면에서 부른다 — 기본은 "미인증"으로 두어 화면 테스트가
  // 사용자 메뉴를 신경쓰지 않게 한다.
  http.get("*/api/auth/me", () => HttpResponse.json({ authenticated: false },
                                                    { status: 401 })),
];
