import { describe, expect, it } from "vitest";

import { MANUAL_ORDER, manualFor } from "@/content/manual";

import { filterSections, sectionText } from "./manualSearch";

const ko = manualFor("ko");
const en = manualFor("en");
const koSections = MANUAL_ORDER.map((id) => ko[id]);
const enSections = MANUAL_ORDER.map((id) => en[id]);

describe("sectionText", () => {
  it("제목·요약·본문·목록·명령어·도식 라벨을 모두 담는다", () => {
    const text = sectionText(ko.operations);
    expect(text).toContain(ko.operations.title);
    expect(text).toContain(ko.operations.lede);
    // 명령어는 검색으로 찾을 수 있어야 한다 — 운영자가 기억하는 것은 문장이
    // 아니라 명령어다.
    expect(text).toContain("cdk deploy");
  });

  it("내부 식별자는 담지 않는다", () => {
    // "mockup"이나 heading id가 들어가면 그 단어가 화면에 보이지 않는 절이
    // 검색 결과에 뜬다.
    const text = sectionText(ko.dashboard);
    expect(text).not.toContain("mockup");
    expect(text).not.toContain("progress-meaning");
  });

  it("도식 상자의 라벨을 담는다", () => {
    const text = sectionText(en.intro);
    expect(text).toContain("Start from pain points");
  });
});

describe("filterSections", () => {
  it("빈 질의는 전부 통과시킨다", () => {
    expect(filterSections(koSections, "")).toHaveLength(koSections.length);
    expect(filterSections(koSections, "   ")).toHaveLength(koSections.length);
  });

  it("일치하지 않으면 빈 목록", () => {
    expect(filterSections(koSections, "존재하지않는단어xyz")).toEqual([]);
  });

  it("한국어 본문을 찾는다", () => {
    const hits = filterSections(koSections, "설문");
    expect(hits.map((s) => s.id)).toContain("survey");
  });

  it("영어에서 대소문자를 무시한다", () => {
    const lower = filterSections(enSections, "prototype").map((s) => s.id);
    const upper = filterSections(enSections, "PROTOTYPE").map((s) => s.id);
    expect(upper).toEqual(lower);
    expect(lower).toContain("prototypes");
  });

  it("원본 순서를 유지한다", () => {
    const hits = filterSections(koSections, "프로토타입").map((s) => s.id);
    const order = koSections.map((s) => s.id).filter((id) => hits.includes(id));
    expect(hits).toEqual(order);
  });
});
