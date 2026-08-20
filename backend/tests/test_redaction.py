from aipds.parsers.redaction import redact_credentials

def test_redacts_known_credential_prefixes():
    assert redact_credentials("key AKIAIOSFODNN7EXAMPLE done") == "key [CREDENTIAL REDACTED] done"
    assert redact_credentials("sk-abc123def456ghi789") == "[CREDENTIAL REDACTED]"
    assert redact_credentials("bedrock-api-key-XYZ123456") == "[CREDENTIAL REDACTED]"
    assert redact_credentials("export AWS_BEARER_TOKEN_BEDROCK=zzz999") == "export [CREDENTIAL REDACTED]"

def test_leaves_normal_text_untouched():
    text = "MD가 자연어로 컨셉을 입력하면 30~50개 후보를 받습니다."
    assert redact_credentials(text) == text

def test_does_not_redact_short_or_wordlike_tokens():
    assert redact_credentials("skiing is fun") == "skiing is fun"

def test_does_not_over_redact_hyphenated_words():
    for phrase in [
        "we recommend a risk-mitigation-plan before launch",
        "the desk-research-summary indicates strong demand",
        "a task-oriented-workflow reduces friction",
        "kiosk-deployment-schedule needs revision",
    ]:
        assert redact_credentials(phrase) == phrase

def test_still_redacts_real_sk_key_at_token_start():
    assert redact_credentials("key sk-proj-abc123def456 here") == "key [CREDENTIAL REDACTED] here"
    assert redact_credentials("sk-abc123def456ghi789") == "[CREDENTIAL REDACTED]"
