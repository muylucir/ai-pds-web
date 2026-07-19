def test_event_mirror_has_payload_and_structured_kinds():
    from events import AgentEvent
    ev = AgentEvent(kind="stage", payload='{"stage":"Envision"}')
    assert ev.payload == '{"stage":"Envision"}'
    from claude_driver import AgentEvent as DriverEvent
    assert DriverEvent is AgentEvent  # single definition, no drift
