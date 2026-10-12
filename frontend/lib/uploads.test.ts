import { describe, it, expect } from "vitest";
import { ApiError } from "@/lib/api/client";
import { checkUpload, problemFromError, MAX_UPLOAD_BYTES, UPLOAD_ACCEPT } from "./uploads";

function file(name: string, size = 1): File {
  const f = new File(["x"], name);
  Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("checkUpload", () => {
  it("accepts supported types up to the limit, case-insensitively", () => {
    expect(checkUpload(file("요구사항.DOCX"))).toBeNull();
    expect(checkUpload(file("a.png", MAX_UPLOAD_BYTES))).toBeNull();
  });

  it("names legacy formats separately from unknown ones", () => {
    expect(checkUpload(file("기획서.hwp"))).toBe("legacy");
    expect(checkUpload(file("old.xls"))).toBe("legacy");
    expect(checkUpload(file("run.exe"))).toBe("type");
    expect(checkUpload(file("noext"))).toBe("type");
  });

  it("refuses a file over the limit", () => {
    expect(checkUpload(file("a.pdf", MAX_UPLOAD_BYTES + 1))).toBe("size");
  });
});

describe("problemFromError", () => {
  it("maps the backend's statuses and reasons", () => {
    expect(problemFromError(new ApiError(413, "<html>413 Request Entity Too Large</html>"))).toBe("size");
    expect(problemFromError(new ApiError(415, '{"detail":"unsupported extension: .exe"}'))).toBe("type");
    expect(problemFromError(new ApiError(415, '{"detail":".docx file expands beyond 100MB"}'))).toBe("expanded");
    expect(problemFromError(new ApiError(415, '{"detail":"cannot parse .pdf file: corrupted or invalid"}'))).toBe("damaged");
    expect(problemFromError(new ApiError(500, "boom"))).toBe("failed");
    expect(problemFromError(new TypeError("network"))).toBe("failed");
  });
});

it("the picker accepts every supported extension", () => {
  expect(UPLOAD_ACCEPT.split(",")).toContain(".vtt");
  expect(UPLOAD_ACCEPT.split(",")).toContain(".webp");
});
