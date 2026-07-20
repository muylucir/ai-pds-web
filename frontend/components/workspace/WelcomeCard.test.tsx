import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WelcomeCard } from "./WelcomeCard";

describe("WelcomeCard", () => {
  it("sends the Path A message on button click", () => {
    const onStart = vi.fn();
    render(<WelcomeCard onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Path A/ }));
    expect(onStart).toHaveBeenCalledWith(
      "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.");
  });

  it("sends the Path B message on button click", () => {
    const onStart = vi.fn();
    render(<WelcomeCard onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Path B/ }));
    expect(onStart).toHaveBeenCalledWith(
      "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.");
  });

  it("mentions free-form input", () => {
    render(<WelcomeCard onStart={vi.fn()} />);
    expect(screen.getByText(/직접 입력해도/)).toBeInTheDocument();
  });
});
