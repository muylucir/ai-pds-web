// frontend/components/ImportProjectButton.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ImportProjectButton } from "./ImportProjectButton";

const STORAGE = "https://bucket.s3.example.invalid/imports/abc";

const RESULT = {
  project_id: "origin-proj", name: "원본", language: "ko", model_id: null,
  source_project_id: "origin-proj",
  counts: { objects: 12, source_files: 4, prototypes: 1 },
  warnings: [] as { code: string; count: number }[],
};

function bundle(): File {
  return new File([new Uint8Array(8)], "aipds-export.zip",
                  { type: "application/zip" });
}

/** presign + PUT을 세운다. `importResponses`는 POST /project-imports의 응답을
 *  호출 순서대로 돌려준다 — 409 뒤의 재시도를 표현하기 위한 것이다. */
function wire(importResponses: (() => Response)[]) {
  const bodies: any[] = [];
  let ticketCalls = 0;
  server.use(
    http.post(`${API_BASE_URL}/project-imports/uploads`, () => {
      ticketCalls += 1;
      return HttpResponse.json({
        upload_id: "abc", url: STORAGE, content_type: "application/zip",
        expires_in: 900, max_bytes: 1024,
      }, { status: 201 });
    }),
    http.put(STORAGE, () => new HttpResponse(null, { status: 200 })),
    http.post(`${API_BASE_URL}/project-imports`, async ({ request }) => {
      bodies.push(await request.json());
      const next = importResponses.shift();
      return next ? next() : HttpResponse.json(RESULT, { status: 201 });
    }),
  );
  return { bodies, ticketCalls: () => ticketCalls };
}

/** 트리거를 눌러 모달을 연다. 모달 안의 라벨은 열린 뒤에만 존재한다. */
async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "가져오기" }));
  return screen.getByRole("dialog");
}

/** 모달의 주 버튼. 트리거와 이름이 같으므로(둘 다 "가져오기") 대화상자 안에서
 *  찾아야 한다 — 화면 전체에서 찾으면 트리거가 먼저 잡힌다. */
function submitButton(): HTMLElement {
  return within(screen.getByRole("dialog"))
    .getByRole("button", { name: /가져오기|업로드 중|복원 중/ });
}

describe("ImportProjectButton", () => {
  it("uploads the bundle and hands the restored project to the caller", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    const { bodies } = wire([]);
    render(<ImportProjectButton onImported={onImported} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    await waitFor(() => expect(onImported).toHaveBeenCalledWith(RESULT));
    // 대상 id를 지정하지 않았으므로 번들의 원본 id가 쓰인다.
    expect(bodies).toEqual([{ upload_id: "abc" }]);
  });

  it("opens an id field on a collision and retries WITHOUT re-uploading", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    const { bodies, ticketCalls } = wire([
      () => HttpResponse.json({ detail: "project_exists" }, { status: 409 }),
    ]);
    render(<ImportProjectButton onImported={onImported} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    // 충돌 문구와 id 칸이 나타난다.
    await screen.findByText(/이미 존재하는 프로젝트 ID/);
    const idField = screen.getByLabelText("새 프로젝트 ID");

    await user.type(idField, "copy-1");
    await user.click(submitButton());

    await waitFor(() => expect(onImported).toHaveBeenCalled());
    // **핵심**: 같은 upload_id로 재시도했고 티켓을 다시 받지 않았다 —
    // 사용자가 수백 MB를 다시 올리지 않는다.
    expect(bodies).toEqual([
      { upload_id: "abc" },
      { upload_id: "abc", project_id: "copy-1" },
    ]);
    expect(ticketCalls()).toBe(1);
  });

  it("filters characters a project id cannot contain", async () => {
    const user = userEvent.setup();
    wire([() => HttpResponse.json({ detail: "project_exists" }, { status: 409 })]);
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);
    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());
    const idField = await screen.findByLabelText("새 프로젝트 ID");

    await user.type(idField, "새 프로젝트/1");

    expect(idField).toHaveValue("1");
  });

  it("shows warnings before navigating away", async () => {
    // 조용히 넘기면 사용자는 고른 적 없는 모델로 도는 프로젝트를 그것도 모른 채
    // 쓴다 — 이 기능에서 가장 조용한 실패다.
    const user = userEvent.setup();
    const onImported = vi.fn();
    const withWarnings = {
      ...RESULT,
      warnings: [{ code: "model_unavailable", count: 1 },
                 { code: "survey_tokens_reissued", count: 2 }],
    };
    wire([() => HttpResponse.json(withWarnings, { status: 201 })]);
    render(<ImportProjectButton onImported={onImported} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    await screen.findByText(/기본 모델로 시작합니다/);
    expect(screen.getByText(/설문 링크 2개가 새로 발급/)).toBeInTheDocument();
    // 확인하기 전에는 이동하지 않는다.
    expect(onImported).not.toHaveBeenCalled();

    await user.click(submitButton());

    expect(onImported).toHaveBeenCalledWith(withWarnings);
  });

  it("navigates straight through when there is nothing to warn about", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    wire([]);
    render(<ImportProjectButton onImported={onImported} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    await waitFor(() => expect(onImported).toHaveBeenCalled());
    expect(screen.queryByText(/기본 모델로 시작합니다/)).not.toBeInTheDocument();
  });

  it("translates a rejected bundle and forgets the upload", async () => {
    const user = userEvent.setup();
    const { ticketCalls } = wire([
      () => HttpResponse.json({ detail: "export_invalid" }, { status: 400 }),
    ]);
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    await screen.findByText(/AI-PDS 프로젝트 번들이 아니거나 손상/);
    // 400은 스테이징이 지워진 상태다 — id 칸을 열어 재시도를 권하면 안 된다.
    expect(screen.queryByLabelText("새 프로젝트 ID")).not.toBeInTheDocument();

    // 다시 누르면 새로 올린다.
    await user.click(submitButton());
    await waitFor(() => expect(ticketCalls()).toBe(2));
  });

  it("reports a storage failure as an upload problem", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE_URL}/project-imports/uploads`, () =>
        HttpResponse.json({
          upload_id: "abc", url: STORAGE, content_type: "application/zip",
          expires_in: 900, max_bytes: 1024,
        }, { status: 201 })),
      http.put(STORAGE, () => new HttpResponse("<Error/>", { status: 403 })),
    );
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    // S3의 XML을 화면에 흘리지 않는다.
    await screen.findByText("업로드에 실패했습니다. 다시 시도해 주세요.");
    expect(screen.queryByText(/Error/)).not.toBeInTheDocument();
  });

  it("says so when the server has no import staging configured", async () => {
    const user = userEvent.setup();
    server.use(http.post(`${API_BASE_URL}/project-imports/uploads`, () =>
      HttpResponse.json({ detail: "import_unavailable" }, { status: 503 })));
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);

    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());

    await screen.findByText("이 서버에는 가져오기가 설정되지 않았습니다.");
  });

  it("cannot be submitted before a file is chosen", async () => {
    const user = userEvent.setup();
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);

    expect(submitButton()).toBeDisabled();
  });

  it("keeps the import flow behind a button until it is asked for", () => {
    // 파일 입력을 화면에 늘 펼쳐 두면 드문 조작이 흔한 조작과 같은 무게를 갖는다.
    render(<ImportProjectButton onImported={vi.fn()} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("번들 파일 선택")).not.toBeInTheDocument();
  });

  it("closes on cancel and forgets what was staged", async () => {
    // 붙들고 있으면 다시 열었을 때 무엇이 올라가 있는지 화면에 보이지 않고,
    // 그 상태에서 누른 가져오기가 지난번 파일을 심는다.
    const user = userEvent.setup();
    wire([() => HttpResponse.json({ detail: "project_exists" }, { status: 409 })]);
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);
    await user.upload(screen.getByLabelText("번들 파일 선택"), bundle());
    await user.click(submitButton());
    await screen.findByLabelText("새 프로젝트 ID");

    await user.click(within(screen.getByRole("dialog"))
      .getByRole("button", { name: "취소" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openDialog(user);
    // 새로 열린 모달에는 id 칸도, 오류도 없다 — 처음 상태다.
    expect(screen.queryByLabelText("새 프로젝트 ID")).not.toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    render(<ImportProjectButton onImported={vi.fn()} />);
    await openDialog(user);

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
