// @vitest-environment node
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import {
  API_BASE_URL,
  ApiError,
  createProject,
  listProjects,
  getState,
  getDocument,
  listQuestionFiles,
  getQuestionFile,
  putAnswers,
  listArtifacts,
  readArtifact,
  postMessage,
  getPending,
  getHistory,
  uploadFile,
} from "./client";

describe("Content-Type header behavior", () => {
  it("omits Content-Type on bodyless GET but sets it on POST", async () => {
    let getCT: string | null = "unset";
    let postCT: string | null = "unset";
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, ({ request }) => {
        getCT = request.headers.get("content-type");
        return HttpResponse.json({ project_type: null, current_stage: null, stages: [] });
      }),
      http.post(`${API_BASE_URL}/projects`, ({ request }) => {
        postCT = request.headers.get("content-type");
        return HttpResponse.json({ project_id: "p1", name: null });
      }),
    );
    await getState("p1");
    await createProject("p1");
    expect(getCT).toBeNull();
    expect(postCT).toContain("application/json");
  });
});

describe("api client request shaping + response typing", () => {
  it("createProject POSTs {project_id,name} and returns the summary", async () => {
    let seenBody: unknown;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ project_id: "p1", name: "기획전 AI 어시스턴트" });
      }),
    );
    const r = await createProject("p1", "기획전 AI 어시스턴트");
    expect(seenBody).toEqual({ project_id: "p1", name: "기획전 AI 어시스턴트" });
    expect(r).toEqual({ project_id: "p1", name: "기획전 AI 어시스턴트" });
  });

  it("createProject omits name when not given", async () => {
    let seenBody: any;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ project_id: "p2", name: null });
      }),
    );
    await createProject("p2");
    expect(seenBody).toEqual({ project_id: "p2" });
  });

  it("createProject maps 409 to ApiError(409)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ detail: "project exists" }, { status: 409 }),
      ),
    );
    await expect(createProject("dup")).rejects.toMatchObject({ status: 409, detail: "project exists" });
    await expect(createProject("dup")).rejects.toBeInstanceOf(ApiError);
  });

  it("listProjects unwraps {projects:[...]}", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ projects: [{ project_id: "a", name: "A" }, { project_id: "b", name: null }] }),
      ),
    );
    const r = await listProjects();
    expect(r.map((p) => p.project_id)).toEqual(["a", "b"]);
  });

  it("getState returns ProjectState", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, () =>
        HttpResponse.json({ project_type: "Greenfield", current_stage: "Product Strategy", stages: [] }),
      ),
    );
    expect((await getState("p1")).project_type).toBe("Greenfield");
  });

  it("getDocument unwraps {markdown}", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/document`, () => HttpResponse.json({ markdown: "# Doc" })),
    );
    expect(await getDocument("p1")).toBe("# Doc");
  });

  it("listQuestionFiles / listArtifacts unwrap their arrays", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions`, () =>
        HttpResponse.json({ questions: ["aiplc-docs/a-questions.md"] }),
      ),
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/audit.md"] }),
      ),
    );
    expect(await listQuestionFiles("p1")).toEqual(["aiplc-docs/a-questions.md"]);
    expect(await listArtifacts("p1")).toEqual(["aiplc-docs/audit.md"]);
  });

  it("readArtifact encodes a slash-bearing path but keeps separators and unwraps {content}", async () => {
    const path = "aiplc-docs/discovery/discovery-document.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${path}`, () =>
        HttpResponse.json({ content: "# Doc" }),
      ),
    );
    expect(await readArtifact("p1", path)).toBe("# Doc");
  });

  it("readArtifact maps 403 to ApiError(403)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/uploads/x.md`, () =>
        HttpResponse.json({ detail: "artifacts only" }, { status: 403 }),
      ),
    );
    await expect(readArtifact("p1", "uploads/x.md")).rejects.toMatchObject({ status: 403 });
  });

  it("getQuestionFile encodes a slash-bearing name path but keeps separators", async () => {
    const name = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions/${name}`, () =>
        HttpResponse.json({
          name: "strategy-questions.md",
          preamble: null,
          questions: [],
          parse_ok: true,
          raw_markdown: null,
        }),
      ),
    );
    const qf = await getQuestionFile("p1", name);
    expect(qf.parse_ok).toBe(true);
  });

  it("getQuestionFile maps 404 to ApiError(404)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions/missing.md`, () =>
        HttpResponse.json({ detail: "question file not found" }, { status: 404 }),
      ),
    );
    await expect(getQuestionFile("p1", "missing.md")).rejects.toMatchObject({ status: 404 });
  });

  it("putAnswers PUTs {answers} and returns reparsed QuestionFile; 400 → ApiError(400)", async () => {
    const name = "aiplc-docs/strategy-questions.md";
    let seenBody: unknown;
    server.use(
      http.put(`${API_BASE_URL}/projects/p1/questions/${name}`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({
          name: "strategy-questions.md",
          preamble: null,
          questions: [{ number: 1, category: null, text: "?", options: [], answer: "B" }],
          parse_ok: true,
          raw_markdown: null,
        });
      }),
    );
    const qf = await putAnswers("p1", name, { "1": "B" });
    expect(seenBody).toEqual({ answers: { "1": "B" } });
    expect(qf.questions[0].answer).toBe("B");

    server.use(
      http.put(`${API_BASE_URL}/projects/p1/questions/${name}`, () =>
        HttpResponse.json({ detail: "bad key" }, { status: 400 }),
      ),
    );
    await expect(putAnswers("p1", name, { "99": "A" })).rejects.toMatchObject({ status: 400 });
  });

  it("postMessage POSTs {text} and returns TurnResult", async () => {
    let seenBody: unknown;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/message`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ events: [{ kind: "message", text: "ok", path: null }, { kind: "done", text: null, path: null }] });
      }),
    );
    const tr = await postMessage("p1", "승인");
    expect(seenBody).toEqual({ text: "승인" });
    expect(tr.events.map((e) => e.kind)).toEqual(["message", "done"]);
  });

  it("getPending unwraps {pending} to the raw JSON string (or null)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/pending`, () =>
        HttpResponse.json({ pending: '{"interrupt_id":"i-1","questions":{}}' }),
      ),
    );
    expect(await getPending("p1")).toBe('{"interrupt_id":"i-1","questions":{}}');

    server.use(
      http.get(`${API_BASE_URL}/projects/p2/pending`, () => HttpResponse.json({ pending: null })),
    );
    expect(await getPending("p2")).toBeNull();
  });

  it("getHistory GETs /history and returns items", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/history`, () =>
        HttpResponse.json({ items: [{ role: "user", text: "hi", card: null, name: null }] }),
      ),
    );
    expect(await getHistory("p1")).toEqual([{ role: "user", text: "hi", card: null, name: null }]);
  });

  it("uploadFile POSTs multipart and returns the stored path", async () => {
    let form: FormData | undefined;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/uploads`, async ({ request }) => {
        form = await request.formData();
        return HttpResponse.json({ path: "uploads/a.md", chars: 3, truncated: false });
      }),
    );
    const r = await uploadFile("p1", new File(["abc"], "a.md", { type: "text/markdown" }));
    expect((form!.get("file") as File).name).toBe("a.md");
    expect(r.path).toBe("uploads/a.md");
  });

  it("uploadFile maps a non-OK response to ApiError", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/uploads`, () =>
        HttpResponse.text("file too large", { status: 413 }),
      ),
    );
    await expect(uploadFile("p1", new File(["abc"], "a.md"))).rejects.toMatchObject({ status: 413 });
  });
});
