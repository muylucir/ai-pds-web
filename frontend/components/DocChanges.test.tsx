import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { DocChanges } from "./DocChanges";

const PATH = "aiplc-docs/discovery/envision/pain-points.md";
const QFILE = "aiplc-docs/discovery/envision/pain-point-questions.md";
const BEFORE = "# 페인 포인트\n\n- 재고 확인이 느리다\n\n## 배송\n\n택배만 쓴다.\n";
const AFTER = "# 페인 포인트\n\n- 재고 확인이 느리다\n- 반품 처리가 수작업이다\n\n## 배송\n\n택배와 퀵을 쓴다.\n";

function baselines(list: object[], before: string | null = BEFORE) {
  server.use(
    http.get(`${API_BASE_URL}/projects/p1/revisions`, () => HttpResponse.json({ baselines: list })),
    http.get(`${API_BASE_URL}/projects/p1/revisions/:rid`, () => HttpResponse.json({ content: before })),
  );
}

const ROUND = { id: "2026-10-27T05-20-11Z-0123abcd", at: "2026-10-27T05:20:11+00:00",
                kind: "answers", source: QFILE, existed: true };

async function show(path = PATH, text = AFTER) {
  await act(async () => {
    render(<DocChanges projectId="p1" path={path} text={text} />);
  });
}

describe("DocChanges", () => {
  it("renders the plain document when there is nothing to compare", async () => {
    await show();
    expect(screen.getByText("반품 처리가 수작업이다")).toBeInTheDocument();
    expect(screen.queryByText("변경 표시")).not.toBeInTheDocument();
  });

  it("marks what changed since the answers and names the baseline", async () => {
    baselines([ROUND]);
    await show();
    expect(await screen.findByText("추가됨")).toBeInTheDocument();
    expect(screen.getByText("수정됨")).toBeInTheDocument();
    expect(screen.getByText(/pain-point-questions\.md 답변 전/)).toBeInTheDocument();
    expect(screen.getByText(/추가 1 · 수정 1 · 삭제 0/)).toBeInTheDocument();
    // The sections holding a change, by heading — the way to find them in a long document.
    expect(screen.getByRole("button", { name: "페인 포인트" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "배송" })).toBeInTheDocument();
  });

  it("can be turned off to read the document clean", async () => {
    baselines([ROUND]);
    await show();
    await screen.findByText("추가됨");
    await userEvent.click(screen.getByLabelText("변경 표시"));
    expect(screen.queryByText("추가됨")).not.toBeInTheDocument();
    expect(screen.getByText("반품 처리가 수작업이다")).toBeInTheDocument();
  });

  it("says a document is new instead of painting all of it as added", async () => {
    baselines([{ ...ROUND, existed: false }], null);
    await show();
    expect(await screen.findByText(/새로 작성된 문서입니다/)).toBeInTheDocument();
    expect(screen.queryByText("추가됨")).not.toBeInTheDocument();
  });

  it("offers older baselines when there are several", async () => {
    baselines([ROUND, { ...ROUND, id: "2026-10-27T04-00-00Z-0123abcd", at: "2026-10-27T04:00:00+00:00",
                        kind: "revision", source: QFILE }]);
    await show();
    const select = await screen.findByRole("combobox", { name: "비교 기준" });
    expect(select.querySelectorAll("option")).toHaveLength(2);
    expect(screen.getByRole("option", { name: /답 변경 전/ })).toBeInTheDocument();
  });

  it("does not ask for baselines of a record that is not a document", async () => {
    let asked = false;
    server.use(http.get(`${API_BASE_URL}/projects/p1/revisions`, () => {
      asked = true;
      return HttpResponse.json({ baselines: [] }, { status: 400 });
    }));
    await show("aiplc-docs/audit.md", "# audit\n");
    expect(screen.getByText("audit")).toBeInTheDocument();
    expect(asked).toBe(false);
    expect(screen.queryByText(/변경 비교를 불러오지 못했습니다/)).not.toBeInTheDocument();
  });
});
