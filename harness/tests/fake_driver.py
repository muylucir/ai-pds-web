from events import AgentEvent


class FakeDriver:
    """Scripted driver: run() yields a fixed event list; records calls."""
    def __init__(self, events=None, pending_payload=None):
        self._events = events or [AgentEvent(kind="message", text="hi"),
                                  AgentEvent(kind="done")]
        self._pending = pending_payload
        self.interrupts = 0
        self.answers_calls: list[tuple[str, dict]] = []

    async def run(self, text):
        for ev in self._events:
            yield ev

    async def submit_answers(self, interrupt_id, answers):
        self.answers_calls.append((interrupt_id, answers))
        return True

    async def interrupt(self):
        self.interrupts += 1

    async def pending(self):
        return self._pending
