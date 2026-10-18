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


# Credential-shaped samples are **assembled at runtime.** As literals, secret scanners flag
# the test samples as real keys (observed with gitleaks and semgrep secrets). The values
# themselves mean nothing; only their shape needs to match the patterns.
def _sample(prefix: str, body: str, n: int) -> str:
    return prefix + (body * n)[:n]


def _jwt_shaped() -> str:
    import base64
    import json

    def part(obj) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return ".".join([part({"alg": "RS256", "kid": "k"}),
                     part({"sub": "s", "scope": "openid"}),
                     part({"sig": "x" * 16})])


def test_redacts_temporary_sts_credentials():
    """What the agent receives are STS temporary credentials (ASIA key, session token); looking
    only for AKIA would let `env` or a broker response go out as is to the stream, audit.md, and S3."""
    key_id = _sample("AS" + "IA", "Z9", 16)
    assert redact_credentials(f"id {key_id} x") == "id [CREDENTIAL REDACTED] x"
    session = _sample("IQoJ" + "b3JpZ2lu", "Ab9+", 120)
    assert redact_credentials(f"t {session} x") == "t [CREDENTIAL REDACTED] x"
    for name in ("AWS_SECRET_ACCESS_KEY", "aws_session_token",
                 "AWS_CONTAINER_AUTHORIZATION_TOKEN"):
        line = name + "=" + _sample("", "q/W+", 40)
        assert redact_credentials(line) == "[CREDENTIAL REDACTED]", name


def test_redacts_jwts():
    assert redact_credentials(f"Bearer {_jwt_shaped()}") == "Bearer [CREDENTIAL REDACTED]"


def test_new_patterns_leave_ordinary_text_alone():
    for text in ("ASIA-Pacific rollout", "the ASIAN market", "eyJ is a prefix",
                 "set AWS_REGION=ap-northeast-2"):
        assert redact_credentials(text) == text


def test_no_pattern_matches_across_whitespace():
    """Invariant that streaming relies on (agent/delta_buffer.py): a match never spans whitespace."""
    from aipds.parsers.redaction import _PATTERNS
    samples = ["AWS_SESSION_TOKEN= abc", "AKIA IOSFODNN7EXAMPLE",
               "eyJabcdefgh .eyJabcdefgh.abcdefgh"]
    for pat in _PATTERNS:
        for sample in samples:
            for m in pat.finditer(sample):
                assert not any(ch.isspace() for ch in m.group(0)), (pat.pattern, sample)
