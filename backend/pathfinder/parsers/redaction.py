import re

# Order matters: match the assignment form (KEY=value) and the standalone token forms.
_PATTERNS = [
    re.compile(r"AWS_BEARER_TOKEN[A-Z_]*=\S+"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"sk-[A-Za-z0-9\-]{10,}"),
    re.compile(r"bedrock-api-key-[A-Za-z0-9\-]{4,}"),
    re.compile(r"goog_[A-Za-z0-9\-]{4,}"),
]

def redact_credentials(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub("[CREDENTIAL REDACTED]", text)
    return text
