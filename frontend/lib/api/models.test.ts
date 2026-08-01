// frontend/lib/api/models.test.ts
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import {
  addModel, deleteModel, listAdminModels, listModels, patchModel,
} from "./models";

describe("models API", () => {
  it("listModels unwraps the models array", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () =>
      HttpResponse.json({ models: [{ name: "Opus 5", model_id: "global.anthropic.claude-opus-5" }] })));
    expect(await listModels()).toEqual([
      { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
    ]);
  });

  it("listModels returns [] when the body is empty", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({})));
    expect(await listModels()).toEqual([]);
  });

  it("listAdminModels keeps the display flag", async () => {
    server.use(http.get(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ models: [{ name: "Opus 5", model_id: "m1", display: false }] })));
    expect(await listAdminModels()).toEqual([
      { name: "Opus 5", model_id: "m1", display: false },
    ]);
  });

  it("addModel posts name, model_id and display", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    const created = await addModel("Opus 4.8", "global.anthropic.claude-opus-4-8", true);
    expect(body).toEqual({ name: "Opus 4.8",
                           model_id: "global.anthropic.claude-opus-4-8",
                           display: true });
    expect(created.display).toBe(true);
  });

  it("addModel surfaces the server's 400 detail", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ detail: "at most 5 models can be displayed" }, { status: 400 })));
    await expect(addModel("여섯", "m6", true)).rejects.toMatchObject({
      status: 400, detail: "at most 5 models can be displayed",
    });
  });

  it("patchModel sends only the given fields", async () => {
    let body: any;
    server.use(http.patch(`${API_BASE_URL}/admin/models/m1`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ name: "n", model_id: "m1", display: false });
    }));
    await patchModel("m1", { display: false });
    expect(body).toEqual({ display: false });
  });

  it("deleteModel tolerates a 204 with no body", async () => {
    server.use(http.delete(`${API_BASE_URL}/admin/models/m1`, () =>
      new HttpResponse(null, { status: 204 })));
    await expect(deleteModel("m1")).resolves.toBeUndefined();
  });
});
