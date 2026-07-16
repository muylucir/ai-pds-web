from pathfinder.parsers.redaction import redact_credentials

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
