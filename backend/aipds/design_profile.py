"""Brand design profile — a single DESIGN.md uploaded by an admin.

It lives at `design/profile.json` at the bucket root. Same place as `models/catalog.json`, and outside
projects/ for the same reason: it is managed even when there are no projects at all.

**Only the original markdown and meta are stored.** tokens/prose are parsed at read time —
storing derived values alongside would leave the stored object stale whenever the parser is fixed, and that staleness
cannot be told apart on screen.

Tokens are written as `key: value` lines inside a ```tokens code fence. Not a markdown table, so that
errors can be pointed out **by line number** — telling the admin what they wrote wrong at upload time
is the core requirement of this feature, and for that the parser has to know positions.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: Relative to the bucket root. The fifth prefix, next to projects/, sessions/, surveys/ and models/
#: (pairs with BACKEND_BUCKET_PREFIXES in infra/lib/backend-permissions.ts).
DESIGN_PREFIX = "design/"
DESIGN_PROFILE_KEY = f"{DESIGN_PREFIX}profile.json"

#: Upload cap. Not storage capacity but the **context budget** — the prose is loaded as is into every build session's
#: workspace and agent context, and Korean takes 1.66x the tokens for the same content,
#: bringing compaction forward.
MAX_DESIGN_BYTES = 64 * 1024

COLOR_TOKENS: tuple[str, ...] = (
    "primary", "primary_foreground", "secondary", "accent", "destructive",
    "background", "foreground", "muted", "muted_foreground", "border", "ring",
)
LENGTH_TOKENS: tuple[str, ...] = ("radius",)
FONT_TOKENS: tuple[str, ...] = ("font_sans", "font_mono")
#: The key name is `--primary`, which is `bg-primary` — having no mapping table is
#: why this whitelist exists. Accepting arbitrary keys would create a mapping layer, and that layer
#: would go stale along with shadcn versions.
ALLOWED_TOKENS: tuple[str, ...] = COLOR_TOKENS + LENGTH_TOKENS + FONT_TOKENS

_FENCE_OPEN = re.compile(r"^```tokens\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_LENGTH = re.compile(r"^\d+(?:\.\d+)?(?:rem|px)$")
_FONT = re.compile(r"^[A-Za-z0-9 ,'\"_-]+$")

TEMPLATE_MD = """# 브랜드 디자인 프로필

아래 `tokens` 블록의 값을 회사 브랜드로 바꾸세요. **값을 쓰려면 줄 앞의 `#`을
지우세요** — `#`이 남아 있으면 그 줄은 주석으로 무시되고 토큰이 반영되지
않습니다(값만 고치고 `#`을 그대로 두면 서식은 유효하게 통과하지만 토큰이 하나도
반영되지 않는 채로 저장됩니다). 필요 없는 줄은 통째로 지우면 됩니다(모두
선택 항목이고, 지운 값은 shadcn 기본값이 그대로 쓰입니다).

색은 `#rrggbb`, 길이는 `0.75rem`/`12px`, 서체는 폰트 이름을 씁니다. 아래 세
줄은 `#`을 지운 예시입니다 — 그대로 두면 이 값이 브랜드로 적용됩니다.

```tokens
primary: #5b2ea6
radius: 0.75rem
font_sans: Pretendard
# primary_foreground: #ffffff
# secondary: #f1f5f9
# accent: #ede9fe
# destructive: #dc2626
# background: #ffffff
# foreground: #0f172a
# muted: #f8fafc
# muted_foreground: #64748b
# border: #e2e8f0
# ring: #5b2ea6
# font_mono: JetBrains Mono
```

## 톤

(여백, 강조 방식, 서체 크기 성향 등 화면을 만들 때 지켜야 할 것을 적어주세요.)

## 금기

(쓰지 말아야 할 색·표현·패턴을 적어주세요.)
"""


class DesignProfileError(Exception):
    """Profile policy violation. `code` is translated into the route's HTTP status
    (same contract as model_catalog.CatalogError)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DesignProfile(BaseModel):
    filename: str
    uploaded_at: str
    uploaded_by: str
    markdown: str
    #: Not stored — the store fills it with values parsed from markdown.
    tokens: dict[str, str] = {}
    prose: str = ""


def _validate(key: str, value: str, line: int) -> None:
    if key in COLOR_TOKENS and not _HEX.match(value):
        raise DesignProfileError(
            "invalid",
            f"line {line}: {key} must be a hex colour like #5b2ea6 (got {value!r})")
    if key in LENGTH_TOKENS and not _LENGTH.match(value):
        raise DesignProfileError(
            "invalid",
            f"line {line}: {key} must be a length like 0.75rem or 12px "
            f"(got {value!r})")
    if key in FONT_TOKENS and not _FONT.match(value):
        raise DesignProfileError(
            "invalid", f"line {line}: {key} must be a font family name "
                       f"(got {value!r})")


def parse_design_md(text: str) -> tuple[dict[str, str], str]:
    """Returns (tokens, prose). Format violations raise DesignProfileError("invalid").

    The prose is everything except the fence block — it is not transformed. What the agent reads
    must be exactly the sentences the admin wrote.
    """
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    start = next((i for i, ln in enumerate(lines) if _FENCE_OPEN.match(ln)), None)
    if start is None:
        return {}, body.strip()
    end = next((i for i in range(start + 1, len(lines))
                if _FENCE_CLOSE.match(lines[i])), None)
    if end is None:
        raise DesignProfileError(
            "invalid", f"line {start + 1}: the ```tokens block is never closed")

    tokens: dict[str, str] = {}
    for offset, raw in enumerate(lines[start + 1:end], start=start + 2):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise DesignProfileError(
                "invalid", f"line {offset}: expected `key: value` (got {line!r})")
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key not in ALLOWED_TOKENS:
            raise DesignProfileError(
                "invalid", f"line {offset}: unknown token {key!r} "
                           f"(allowed: {', '.join(ALLOWED_TOKENS)})")
        if key in tokens:
            raise DesignProfileError(
                "invalid", f"line {offset}: duplicate token {key!r}")
        _validate(key, value, offset)
        tokens[key] = value

    prose = "\n".join(lines[:start] + lines[end + 1:]).strip()
    return tokens, prose


class DesignProfileStore:
    """Reading/writing the profile. Read-only if `s3` is None (bucket not configured)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> DesignProfile | None:
        """None if missing. None if corrupt, too. None if S3 fails for any other reason as well.

        Why it does not raise is the same as ModelCatalog falling back to the seed:
        raising here would let a single hand-edited JSON **block start() for every build
        session.** With None, things keep running without the brand and the cause stays in the log.

        `get` itself is wrapped broadly too — at first only FileNotFoundError (missing)
        was caught, but every other S3 exception, such as AccessDenied, throttling and network errors,
        leaked straight out and broke this function's promise (None if missing or broken). This
        store has to keep that promise inside itself so the callers (session.start(),
        the hosting route) do not have to depend on each wrapping it again.
        """
        if self._s3 is None:
            return None
        try:
            body = await self._s3.get(DESIGN_PROFILE_KEY)
        except FileNotFoundError:
            return None
        except Exception:
            _log.exception("design profile fetch failed at %s; treating as absent",
                           DESIGN_PROFILE_KEY)
            return None
        try:
            d = json.loads(body)
            markdown = d["markdown"]
            tokens, prose = parse_design_md(markdown)
            return DesignProfile(filename=d.get("filename", ""),
                                 uploaded_at=d.get("uploaded_at", ""),
                                 uploaded_by=d.get("uploaded_by", ""),
                                 markdown=markdown, tokens=tokens, prose=prose)
        except Exception:
            _log.exception("corrupt design profile at %s; treating as absent",
                           DESIGN_PROFILE_KEY)
            return None

    async def save(self, *, filename: str, uploaded_by: str,
                   markdown: str) -> DesignProfile:
        """Parsing comes first — on a validation failure nothing is written to S3."""
        if self._s3 is None:
            raise DesignProfileError(
                "readonly",
                "design profile is read-only without AIPDS_S3_BUCKET")
        tokens, prose = parse_design_md(markdown)
        uploaded_at = datetime.now(timezone.utc).isoformat()
        await self._s3.put(DESIGN_PROFILE_KEY, json.dumps({
            "filename": filename,
            "uploaded_at": uploaded_at,
            "uploaded_by": uploaded_by,
            "markdown": markdown,
        }, ensure_ascii=False))
        return DesignProfile(filename=filename, uploaded_at=uploaded_at,
                             uploaded_by=uploaded_by, markdown=markdown,
                             tokens=tokens, prose=prose)

    async def remove(self) -> None:
        """S3StoreLike has no single-key delete, so it deletes by prefix —
        there is only profile.json under design/."""
        if self._s3 is None:
            raise DesignProfileError(
                "readonly",
                "design profile is read-only without AIPDS_S3_BUCKET")
        await self._s3.delete_prefix(DESIGN_PREFIX)
