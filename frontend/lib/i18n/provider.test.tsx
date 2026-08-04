import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LocaleProvider, useT, useLocale } from "./provider";

function Probe() {
  const t = useT();
  const locale = useLocale();
  return <p data-testid="out">{`${locale}:${t("nav.dashboard")}`}</p>;
}

describe("LocaleProvider", () => {
  it("Provider의 로케일로 번역한다", () => {
    render(
      <LocaleProvider locale="en">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("en:Dashboard");
  });

  it("ko Provider는 한국어를 준다", () => {
    render(
      <LocaleProvider locale="ko">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("ko:대시보드");
  });
});

describe("Provider 밖에서의 폴백", () => {
  // 이것이 기존 테스트 535건을 그대로 통과시키는 장치다. 그 테스트들은
  // 컴포넌트를 Provider로 감싸지 않고 render()하므로, 훅이 던지면 전부 깨진다.
  it("Provider 없이도 ko로 동작한다 (던지지 않는다)", () => {
    render(<Probe />);
    expect(screen.getByTestId("out")).toHaveTextContent("ko:대시보드");
  });
});
