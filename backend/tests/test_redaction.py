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


# 자격증명 모양의 표본은 **실행 중에 조립한다.** 리터럴로 두면 비밀 탐지 스캐너가 테스트
# 표본을 진짜 키로 집는다(gitleaks·semgrep secrets 실측). 값 자체는 의미가 없다 — 모양만
# 패턴과 맞으면 된다.
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
    """에이전트가 받는 것은 STS 임시 자격증명이다(ASIA 키, 세션 토큰) — AKIA만 보면
    `env`나 브로커 응답이 그대로 스트림·audit.md·S3로 나간다."""
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
    """스트리밍이 기대는 불변식(agent/delta_buffer.py): 매칭이 공백을 넘지 않는다."""
    from aipds.parsers.redaction import _PATTERNS
    samples = ["AWS_SESSION_TOKEN= abc", "AKIA IOSFODNN7EXAMPLE",
               "eyJabcdefgh .eyJabcdefgh.abcdefgh"]
    for pat in _PATTERNS:
        for sample in samples:
            for m in pat.finditer(sample):
                assert not any(ch.isspace() for ch in m.group(0)), (pat.pattern, sample)
