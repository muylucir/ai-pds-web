import { describe, it, expect } from "vitest";
import { DEFAULT_LOCALE, LANG_COOKIE, isLocale, dictFor } from "./index";
import { ko } from "./ko";
import { en } from "./en";

describe("locale basics", () => {
  it("기본 로케일은 ko다 — 쿠키가 없는 기존 사용자와 테스트가 현재 화면을 그대로 본다", () => {
    expect(DEFAULT_LOCALE).toBe("ko");
  });

  it("쿠키 이름을 한 곳에서만 정한다", () => {
    expect(LANG_COOKIE).toBe("pf_lang");
  });

  it("isLocale은 두 값만 통과시킨다", () => {
    expect(isLocale("ko")).toBe(true);
    expect(isLocale("en")).toBe(true);
    expect(isLocale("ja")).toBe(false);
    expect(isLocale("")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
    expect(isLocale(null)).toBe(false);
    expect(isLocale(5)).toBe(false);
  });

  it("dictFor가 로케일별 딕셔너리를 준다", () => {
    expect(dictFor("ko")).toBe(ko);
    expect(dictFor("en")).toBe(en);
  });
});

describe("dictionary key parity", () => {
  // en.ts는 타입으로 ko.ts의 키 집합을 강제받지만(Record<keyof typeof ko, string>),
  // 그 강제는 컴파일 시점이고 vitest는 타입을 검사하지 않는다. 이 테스트가
  // 런타임 회귀 방지다 — 키가 어긋나면 화면에 undefined가 뜬다.
  it("두 딕셔너리의 키 집합이 정확히 같다", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ko).sort());
  });

  it("어느 값도 비어 있지 않다", () => {
    for (const [k, v] of Object.entries({ ...ko, ...en })) {
      expect(v.trim(), `빈 값: ${k}`).not.toBe("");
    }
  });
});
