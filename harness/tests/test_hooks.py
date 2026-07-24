from hooks import build_hooks_app, sdk_diagnostic


def test_sdk_diagnostic_reports_import_failure_without_crashing(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "claude_agent_sdk":
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    out = sdk_diagnostic()
    assert "import failed" in out
