import { describe, it, expect } from "vitest";
import { errorMessage, knownErrorMessage } from "./errorMessage";
import { dictFor, type Dict } from "@/lib/i18n";

function tFor(locale: "ko" | "en") {
  const dict = dictFor(locale);
  return (key: keyof Dict) => dict[key];
}

describe("errorMessage", () => {
  it("아는 코드를 UI 언어로 번역한다", () => {
    expect(errorMessage(tFor("ko"), "email_exists")).toBe("이미 등록된 이메일입니다.");
    expect(errorMessage(tFor("en"), "email_exists")).toBe("That email is already registered.");
  });

  it("모르는 코드는 원문을 그대로 보여준다", () => {
    // 코드화하지 않은 에러가 빈 화면이 아니라 읽을 수 있는 무언가로 보여야 한다.
    expect(errorMessage(tFor("en"), "some_new_error")).toBe("some_new_error");
  });

  it("백엔드가 여전히 한국어 문장을 보내면 그대로 보여준다", () => {
    // 코드화가 부분적으로 진행된 중간 상태에서도 화면이 깨지지 않는다.
    const sentence = "무언가 실패했습니다.";
    expect(errorMessage(tFor("en"), sentence)).toBe(sentence);
  });

  it("빈 detail은 일반 실패 문구가 된다", () => {
    expect(errorMessage(tFor("en"), "")).toBe("The request failed.");
    expect(errorMessage(tFor("ko"), "")).toBe("요청이 실패했습니다.");
  });

  it("진단 정보가 붙은 코드는 코드만 번역하고 상세를 괄호로 덧붙인다", () => {
    // init_incomplete:s3,host — 무엇이 실패했는지가 진단에 필요하다.
    expect(errorMessage(tFor("en"), "init_incomplete:s3,host")).toBe(
      "Initialization did not finish — please try again. (s3,host)",
    );
  });

  it("knownErrorMessage는 아는 코드만 번역하고 나머지는 대체 문구다", () => {
    // 호스팅 시작의 502 detail은 npm 로그 꼬리다 — 한 줄 오류 자리에 원문을 싣지 않는다.
    expect(knownErrorMessage(tFor("en"), "build_session_active", "fallback"))
      .toBe(dictFor("en")["err.buildSessionActive"]);
    expect(knownErrorMessage(tFor("en"), "npm ERR! code ELIFECYCLE", "fallback")).toBe("fallback");
    expect(knownErrorMessage(tFor("en"), "constructor", "fallback")).toBe("fallback");
  });

  it("열린 세션 안내는 실제 버튼 이름을 부른다", () => {
    // 문구가 지목하는 버튼 라벨이 바뀌면 안내가 없는 버튼을 가리킨다.
    for (const locale of ["ko", "en"] as const) {
      const dict = dictFor(locale);
      expect(dict["err.buildSessionActive"]).toContain(`“${dict["proto.done"]}”`);
      expect(dict["err.buildSessionActive"]).toContain(`“${dict["proto.openSession"]}”`);
    }
  });
});
