import re

# Order matters: match the assignment form (KEY=value) and the standalone token forms.
#
# **Every pattern matches inside a whitespace-free run only.** Streaming relies on it:
# agent/delta_buffer.py holds back the trailing non-whitespace run of each delta, which
# is enough only if no match can cross whitespace. A pattern that allows a space
# (`KEY = value`, `"Key": "value"`) would leak through the streamed text silently.
_PATTERNS = [
    re.compile(r"AWS_BEARER_TOKEN[A-Z_]*=\S+"),
    # Environment-style assignments of the credentials an agent can see: the STS
    # secret and session token, and the loopback broker's token (aipds/credentials.py).
    re.compile(r"(?i)\b(?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN"
               r"|AWS_CONTAINER_AUTHORIZATION_TOKEN)=\S+"),
    # Long-term (AKIA) and temporary STS (ASIA) access key ids.
    re.compile(r"(?<![A-Za-z0-9_])A(?:KI|SI)A[0-9A-Z]{12,}"),
    # STS session tokens. Their base64 starts with a fixed header.
    re.compile(r"(?<![A-Za-z0-9+/=])(?:IQoJb3JpZ2lu|FwoGZXIvYXdz)[A-Za-z0-9+/=]{20,}"),
    # JWTs (Cognito access/id tokens): header.payload.signature, both JSON parts base64url.
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"
               r"\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9_])bedrock-api-key-[A-Za-z0-9\-]{4,}"),
    re.compile(r"(?<![A-Za-z0-9_])goog_[A-Za-z0-9\-]{4,}"),
]

def redact_credentials(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub("[CREDENTIAL REDACTED]", text)
    return text
