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
    // An error that is not code-mapped must show as something readable, not a blank screen.
    expect(errorMessage(tFor("en"), "some_new_error")).toBe("some_new_error");
  });

  it("백엔드가 여전히 한국어 문장을 보내면 그대로 보여준다", () => {
    // The screen does not break even in the intermediate state where code mapping is
    // partially done.
    const sentence = "무언가 실패했습니다.";
    expect(errorMessage(tFor("en"), sentence)).toBe(sentence);
  });

  it("빈 detail은 일반 실패 문구가 된다", () => {
    expect(errorMessage(tFor("en"), "")).toBe("The request failed.");
    expect(errorMessage(tFor("ko"), "")).toBe("요청이 실패했습니다.");
  });

  it("진단 정보가 붙은 코드는 코드만 번역하고 상세를 괄호로 덧붙인다", () => {
    // init_incomplete:s3,host — what failed is needed for diagnosis.
    expect(errorMessage(tFor("en"), "init_incomplete:s3,host")).toBe(
      "Initialization did not finish — please try again. (s3,host)",
    );
  });

  it("knownErrorMessage는 아는 코드만 번역하고 나머지는 대체 문구다", () => {
    // The 502 detail from hosting start is the tail of the npm log — the raw text is not put
    // in the one-line error slot.
    expect(knownErrorMessage(tFor("en"), "build_session_active", "fallback"))
      .toBe(dictFor("en")["err.buildSessionActive"]);
    expect(knownErrorMessage(tFor("en"), "npm ERR! code ELIFECYCLE", "fallback")).toBe("fallback");
    expect(knownErrorMessage(tFor("en"), "constructor", "fallback")).toBe("fallback");
  });

  it("열린 세션 안내는 실제 버튼 이름을 부른다", () => {
    // If the button label the message names changed, the guidance would point to a
    // nonexistent button.
    for (const locale of ["ko", "en"] as const) {
      const dict = dictFor(locale);
      expect(dict["err.buildSessionActive"]).toContain(`“${dict["proto.done"]}”`);
      expect(dict["err.buildSessionActive"]).toContain(`“${dict["proto.openSession"]}”`);
      // The same rejection from export (project list) names the tab first.
      for (const key of ["nav.prototypes", "proto.openSession", "proto.done"] as const) {
        expect(dict["transfer.buildSessionActive"]).toContain(`“${dict[key]}”`);
      }
    }
  });
});
