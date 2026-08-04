import { describe, it, expect } from "vitest";
import { errorMessage } from "./errorMessage";
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
});
