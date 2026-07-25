import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "./client";
import {
  createSurvey, getSurvey, closeSurvey, surveyCsvUrl,
  getPublicSurvey, submitPublicSurvey, SurveyClosedError, synthesizeSurvey,
} from "./surveys";

const PID = "p1";
const SLUG = "demo";

const QUESTIONS = [
  { id: "q1", text: "유용?", type: "scale", options: [], required: true },
  { id: "q2", text: "어느 것?", type: "choice", options: ["A", "B"], required: true },
];

describe("surveys api", () => {
  it("createSurvey returns token and url", async () => {
    server.use(http.post(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => HttpResponse.json({ token: "tok", url: "/survey/tok", questions: QUESTIONS },
        { status: 201 })));
    const out = await createSurvey(PID, SLUG);
    expect(out.token).toBe("tok");
    expect(out.url).toBe("/survey/tok");
  });

  it("getSurvey returns null on 404 (no survey yet)", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => new HttpResponse(null, { status: 404 })));
    expect(await getSurvey(PID, SLUG)).toBeNull();
  });

  it("getSurvey returns questionnaire + rollup", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => HttpResponse.json({
        questionnaire: { token: "tok", status: "open", title: "t", hypothesis: "h",
                         questions: QUESTIONS, created_at: "x", closed_at: null },
        url: "/survey/tok",
        rollup: { count: 2, rebuilt_at: "x", per_question: {
          q1: { type: "scale", n: 2, mean: 4.5, distribution: { "1": 0, "2": 0, "3": 0, "4": 1, "5": 1 } },
          q2: { type: "choice", n: 2, counts: { A: 2, B: 0 } },
        } },
      })));
    const view = await getSurvey(PID, SLUG);
    expect(view?.rollup.count).toBe(2);
    expect(view?.questionnaire.status).toBe("open");
  });

  it("closeSurvey resolves on 204", async () => {
    server.use(http.post(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/close`,
      () => new HttpResponse(null, { status: 204 })));
    await expect(closeSurvey(PID, SLUG)).resolves.toBeUndefined();
  });

  it("surveyCsvUrl points at the export route", () => {
    expect(surveyCsvUrl(PID, SLUG)).toBe(
      `${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/responses.csv`);
  });

  it("getPublicSurvey returns questions", async () => {
    server.use(http.get(`${API_BASE_URL}/survey/tok`,
      () => HttpResponse.json({ title: "t", hypothesis: "h", questions: QUESTIONS })));
    const s = await getPublicSurvey("tok");
    expect(s.questions).toHaveLength(2);
  });

  it("getPublicSurvey throws SurveyClosedError on 410", async () => {
    server.use(http.get(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 410 })));
    await expect(getPublicSurvey("tok")).rejects.toBeInstanceOf(SurveyClosedError);
  });

  it("submitPublicSurvey resolves on 204 and throws SurveyClosedError on 410", async () => {
    server.use(http.post(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 204 })));
    await expect(submitPublicSurvey("tok", { q1: 4 })).resolves.toBeUndefined();

    server.use(http.post(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 410 })));
    await expect(submitPublicSurvey("tok", { q1: 4 }))
      .rejects.toBeInstanceOf(SurveyClosedError);
  });
});

describe("synthesizeSurvey", () => {
  it("posts to the synthesize route and returns the written path", async () => {
    server.use(http.post(
      `${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/synthesize`,
      () => HttpResponse.json({
        path: "aiplc-docs/discovery/prototype/validation-results.md",
        response_count: 4,
      })));
    const out = await synthesizeSurvey(PID, SLUG);
    expect(out.path).toBe("aiplc-docs/discovery/prototype/validation-results.md");
    expect(out.response_count).toBe(4);
  });

  it("rejects when there is no survey", async () => {
    server.use(http.post(
      `${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/synthesize`,
      () => new HttpResponse(null, { status: 404 })));
    await expect(synthesizeSurvey(PID, SLUG)).rejects.toBeTruthy();
  });
});
