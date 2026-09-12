// frontend/lib/api/transfer.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import {
  exportProject, filenameFromDisposition, finishImport, stageBundle,
} from "./transfer";

const STORAGE = "https://bucket.s3.example.invalid/imports/abc";

describe("filenameFromDisposition", () => {
  it("prefers the RFC 5987 form so a Korean project id survives", () => {
    // ASCII 폴백은 한글이 하이픈으로 뭉개진 이름이다 — 순서를 뒤집으면
    // 한국어 사용자가 항상 그 이름을 받는다.
    const header = 'attachment; filename="-----.zip"; '
      + "filename*=UTF-8''%ED%95%9C%EA%B8%80.zip";
    expect(filenameFromDisposition(header)).toBe("한글.zip");
  });

  it("falls back to the ASCII form", () => {
    expect(filenameFromDisposition('attachment; filename="pilot2.zip"'))
      .toBe("pilot2.zip");
  });

  it("falls back to the ASCII form when the extended value is undecodable", () => {
    const header = 'attachment; filename="ok.zip"; filename*=UTF-8\'\'%E0%A4%A';
    expect(filenameFromDisposition(header)).toBe("ok.zip");
  });

  it("is null when there is no header", () => {
    expect(filenameFromDisposition(null)).toBeNull();
    expect(filenameFromDisposition("attachment")).toBeNull();
  });
});

describe("exportProject", () => {
  let clicked: HTMLAnchorElement[];
  let revoked: string[];

  beforeEach(() => {
    clicked = [];
    revoked = [];
    // jsdom은 이 둘을 구현하지 않는다.
    (URL as unknown as { createObjectURL: unknown }).createObjectURL =
      vi.fn(() => "blob:fake");
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL =
      vi.fn((u: string) => { revoked.push(u); });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("downloads the bundle under the server's filename", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/pilot2/export`, () =>
      new HttpResponse(new Blob(["PK"]), {
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition": 'attachment; filename="pilot2-export.zip"',
        },
      })));

    await exportProject("pilot2");

    expect(clicked).toHaveLength(1);
    expect(clicked[0].download).toBe("pilot2-export.zip");
    // 오브젝트 URL을 놓아 준다 — 100MB짜리가 탭 수명 내내 살아 있으면 안 된다.
    expect(revoked).toEqual(["blob:fake"]);
  });

  it("names the file itself when the proxy dropped the header", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/pilot2/export`, () =>
      new HttpResponse(new Blob(["PK"]))));

    await exportProject("pilot2");

    expect(clicked[0].download).toBe("aipds-export-pilot2.zip");
  });

  it("escapes the project id in the path", async () => {
    let seen = "";
    server.use(http.get(`${API_BASE_URL}/projects/:pid/export`, ({ params }) => {
      seen = params.pid as string;
      return new HttpResponse(new Blob(["PK"]));
    }));

    await exportProject("한글 프로젝트");

    expect(seen).toBe("한글 프로젝트");
  });

  it("surfaces the backend code instead of downloading an error page", async () => {
    // 진행 중인 빌드 세션(409)이 흔한 실패다 — 앵커 내비게이션이면 사용자가
    // JSON 페이지를 보게 된다.
    server.use(http.get(`${API_BASE_URL}/projects/pilot2/export`, () =>
      HttpResponse.json({ detail: "build_session_active" }, { status: 409 })));

    await expect(exportProject("pilot2")).rejects.toMatchObject({
      status: 409, detail: "build_session_active",
    });
    expect(clicked).toHaveLength(0);
  });
});

describe("stageBundle", () => {
  function bundle(bytes = 4): File {
    return new File([new Uint8Array(bytes)], "b.zip", { type: "application/zip" });
  }

  it("asks for a ticket with the real size, then PUTs the file", async () => {
    let ticketBody: unknown;
    let putUrl: string | null = null;
    let putContentType: string | null = null;
    server.use(
      http.post(`${API_BASE_URL}/project-imports/uploads`, async ({ request }) => {
        ticketBody = await request.json();
        return HttpResponse.json({
          upload_id: "abc", url: STORAGE, content_type: "application/zip",
          expires_in: 900, max_bytes: 1024,
        }, { status: 201 });
      }),
      http.put(STORAGE, async ({ request }) => {
        putUrl = request.url;
        putContentType = request.headers.get("content-type");
        return new HttpResponse(null, { status: 200 });
      }),
    );

    const uploadId = await stageBundle(bundle(7));

    expect(ticketBody).toEqual({ size_bytes: 7 });
    expect(uploadId).toBe("abc");
    // 티켓이 준 URL 그대로 — 서명이 그 URL에 걸려 있다.
    expect(putUrl).toBe(STORAGE);
    // 서명에 포함된 헤더다 — 값이 다르면 S3가 403을 준다.
    expect(putContentType).toBe("application/zip");
    // 본문의 바이트 일치는 여기서 확인할 수 없다: jsdom의 XHR 샤임이 File을
    // 그대로 싣지 않아 `"[object File]"`이 도착한다. 실제 브라우저에서
    // `xhr.send(File)`은 바이트를 보낸다 — 여기서 단정하면 샤임을 시험하는 것이
    // 되고, 통과하든 실패하든 우리 코드에 대해 아무것도 말해 주지 않는다.
  });

  it("reports a failed PUT as upload_failed, not as the storage's XML", async () => {
    server.use(
      http.post(`${API_BASE_URL}/project-imports/uploads`, () =>
        HttpResponse.json({
          upload_id: "abc", url: STORAGE, content_type: "application/zip",
          expires_in: 900, max_bytes: 1024,
        }, { status: 201 })),
      http.put(STORAGE, () =>
        new HttpResponse("<Error><Code>AccessDenied</Code></Error>",
                         { status: 403 })),
    );

    await expect(stageBundle(bundle())).rejects.toMatchObject({
      detail: "upload_failed",
    });
  });

  it("propagates the ticket refusal without touching storage", async () => {
    let putCalls = 0;
    server.use(
      http.post(`${API_BASE_URL}/project-imports/uploads`, () =>
        HttpResponse.json({ detail: "export_too_large" }, { status: 400 })),
      http.put(STORAGE, () => { putCalls += 1; return new HttpResponse(null); }),
    );

    await expect(stageBundle(bundle())).rejects.toBeInstanceOf(ApiError);
    expect(putCalls).toBe(0);
  });
});

describe("finishImport", () => {
  const result = {
    project_id: "copy", name: "n", language: "ko", model_id: null,
    source_project_id: "origin",
    counts: { objects: 3, source_files: 1, prototypes: 1 },
    warnings: [],
  };

  it("omits project_id when the bundle's own id should be used", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/project-imports`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(result, { status: 201 });
    }));

    expect(await finishImport("abc")).toEqual(result);
    expect(body).toEqual({ upload_id: "abc" });
  });

  it("sends the chosen id on a retry after a collision", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/project-imports`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(result, { status: 201 });
    }));

    await finishImport("abc", "copy");

    // 같은 upload_id다 — 재업로드 없이 재시도한다.
    expect(body).toEqual({ upload_id: "abc", project_id: "copy" });
  });

  it("raises the backend code on a collision", async () => {
    server.use(http.post(`${API_BASE_URL}/project-imports`, () =>
      HttpResponse.json({ detail: "project_exists" }, { status: 409 })));

    await expect(finishImport("abc")).rejects.toMatchObject({
      status: 409, detail: "project_exists",
    });
  });
});
