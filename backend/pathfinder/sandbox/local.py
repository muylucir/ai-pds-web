# backend/pathfinder/sandbox/local.py
from __future__ import annotations
import json
from pathlib import Path
from typing import AsyncIterator, Callable
from pathfinder.sandbox.base import Sandbox, AgentEvent

AgentScript = Callable[[str, "LocalSandbox"], list[AgentEvent]]

def _default_script(text: str, sb: "LocalSandbox") -> list[AgentEvent]:
    return [AgentEvent(kind="message", text=f"echo: {text}"), AgentEvent(kind="done")]

# Demo questions payload — mirrors the QuestionFile shape (pathfinder.models)
# exactly, so the frontend 3-pane workspace can be developed/e2e-tested
# against a realistic shape without AWS/microvm. Real methodology questions
# come from the harness in microvm mode; this is a fixed local stand-in.
_DEMO_QUESTIONS = {
    "name": "pain-point-questions",
    "preamble": "데모 시나리오입니다 — 실제 방법론 질문은 microvm 모드에서 생성됩니다.",
    "questions": [
        {"number": 1, "category": "고객", "text": "주요 사용자는 누구인가요?", "answer": None,
         "options": [
             {"letter": "A", "text": "사내 PM", "is_other": False, "recommended": True},
             {"letter": "B", "text": "외부 고객", "is_other": False, "recommended": False},
             {"letter": "X", "text": "Other", "is_other": True, "recommended": False}]},
        {"number": 2, "category": "문제", "text": "가장 큰 페인포인트는?", "answer": None,
         "options": [
             {"letter": "A", "text": "도구 접근성", "is_other": False, "recommended": True},
             {"letter": "B", "text": "속도", "is_other": False, "recommended": False},
             {"letter": "X", "text": "Other", "is_other": True, "recommended": False}]}],
}


def _structured_first_turn(text: str, sb: "LocalSandbox") -> list[AgentEvent]:
    payload = json.dumps({"interrupt_id": "local-i-1", "questions": _DEMO_QUESTIONS},
                          ensure_ascii=False)
    return [
        AgentEvent(kind="message", text=f"'{text}' 요청을 받았습니다. 질문을 준비합니다."),
        AgentEvent(kind="stage", payload=json.dumps(
            {"stage": "Envision", "status": "in_progress", "summary": "질문 생성"},
            ensure_ascii=False)),
        AgentEvent(kind="questions", payload=payload),
        AgentEvent(kind="done"),
    ]

class LocalSandbox(Sandbox):
    def __init__(self, root: Path, script: AgentScript | None = None):
        self.root = Path(root)
        self._script = script or _structured_first_turn
        self._pending_payload: str | None = None

    def _resolve(self, rel_path: str) -> Path:
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"unsafe path: {rel_path}")
        return self.root / rel_path

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    async def read_file(self, rel_path: str) -> str:
        return self._resolve(rel_path).read_text(encoding="utf-8")

    async def write_file(self, rel_path: str, content: str) -> None:
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def list_files(self, glob: str) -> list[str]:
        if glob.startswith("/") or ".." in Path(glob).parts:
            raise ValueError(f"unsafe glob: {glob}")
        return [str(p.relative_to(self.root)) for p in self.root.glob(glob) if p.is_file()]

    # Deliberate: this is an async-generator function (uses `yield`), even though the
    # Sandbox ABC declares send_message as a plain method returning AsyncIterator. Calling
    # an async-gen function returns an AsyncIterator synchronously (no await needed), so
    # `async for event in sandbox.send_message(text)` works. Do not "fix" this mismatch.
    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for event in self._script(text, self):
            if event.kind == "questions":
                self._pending_payload = event.payload
            yield event

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._pending_payload is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._pending_payload = None
        summary = ", ".join(f"{k}={v}" for k, v in sorted(answers.items()))
        for event in [
            AgentEvent(kind="message", text=f"답변({summary})을 반영했습니다."),
            AgentEvent(kind="stage", payload=json.dumps(
                {"stage": "Envision", "status": "completed", "summary": "답변 반영"},
                ensure_ascii=False)),
            AgentEvent(kind="document", payload=json.dumps(
                {"path": "aiplc-docs/discovery/discovery-document.md",
                 "version": "v1", "summary": "초안 생성"}, ensure_ascii=False)),
            AgentEvent(kind="done"),
        ]:
            yield event

    async def pending(self) -> str | None:
        return self._pending_payload

    async def stop(self) -> None:
        pass
