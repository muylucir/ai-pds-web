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

/** Sets up presign + PUT. `importResponses` returns the POST /project-imports responses in call
 *  order — this is for expressing the retry after a 409. */
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

/** Press the trigger to open the modal. The labels inside the modal exist only after it opens. */
async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "가져오기" }));
  return screen.getByRole("dialog");
}

/** The modal's primary button. It has the same name as the trigger (both "가져오기" — import), so
 *  it must be searched for inside the dialog — searching the whole screen catches the trigger
 *  first. */
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
    // No target id was given, so the bundle's original id is used.
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

    // The conflict message and the id field appear.
    await screen.findByText(/이미 존재하는 프로젝트 ID/);
    const idField = screen.getByLabelText("새 프로젝트 ID");

    await user.type(idField, "copy-1");
    await user.click(submitButton());

    await waitFor(() => expect(onImported).toHaveBeenCalled());
    // **Key point**: it retried with the same upload_id and did not get a ticket again —
    // the user does not re-upload hundreds of MB.
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
    // If passed over silently, the user would use a project running on a model they never chose
    // without even knowing it — the quietest failure in this feature.
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
    // It does not navigate before confirmation.
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
    // A 400 means the staging has been deleted — it must not open the id field and suggest a
    // retry.
    expect(screen.queryByLabelText("새 프로젝트 ID")).not.toBeInTheDocument();

    // Pressing again uploads anew.
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

    // S3's XML is not leaked onto the screen.
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
    // Keeping the file input always expanded on screen would give a rare operation the same
    // weight as common ones.
    render(<ImportProjectButton onImported={vi.fn()} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("번들 파일 선택")).not.toBeInTheDocument();
  });

  it("closes on cancel and forgets what was staged", async () => {
    // If it were held on to, reopening would not show on screen what is staged, and pressing
    // import in that state would plant the previous file.
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
    // A newly opened modal has neither the id field nor an error — it is the initial state.
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
