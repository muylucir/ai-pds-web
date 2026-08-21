"""브랜드 디자인 프로필 — admin이 올린 DESIGN.md 한 장.

버킷 루트의 `design/profile.json`에 산다. `models/catalog.json`과 같은 자리,
같은 이유로 projects/ 밖이다: 프로젝트가 하나도 없는 상태에서도 관리된다.

**저장하는 것은 원문 markdown과 메타뿐이다.** tokens/prose는 읽을 때 파싱한다 —
파생값을 함께 저장하면 파서를 고쳤을 때 저장물이 낡고, 그 낡음은 화면에서
구분되지 않는다.

토큰 표기는 ```tokens 코드펜스 안의 `키: 값` 줄이다. 마크다운 표가 아닌 이유는
오류를 **줄 번호로** 짚기 위해서다 — admin이 잘못 쓴 것을 업로드 시점에 알려주는
것이 이 기능의 핵심 요구이고, 그러려면 파서가 위치를 알아야 한다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: 버킷 루트 기준. projects/·sessions/·surveys/·models/ 옆의 다섯 번째 프리픽스다
#: (infra/lib/backend-permissions.ts의 BACKEND_BUCKET_PREFIXES와 짝이다).
DESIGN_PREFIX = "design/"
DESIGN_PROFILE_KEY = f"{DESIGN_PREFIX}profile.json"

#: 업로드 상한. 저장 용량이 아니라 **컨텍스트 예산**이다 — 산문은 매 빌드 세션의
#: 워크스페이스와 에이전트 컨텍스트에 그대로 실리고, 한국어는 같은 내용이 토큰
#: 1.66배를 먹어 컴팩션을 앞당긴다.
MAX_DESIGN_BYTES = 64 * 1024

COLOR_TOKENS: tuple[str, ...] = (
    "primary", "primary_foreground", "secondary", "accent", "destructive",
    "background", "foreground", "muted", "muted_foreground", "border", "ring",
)
LENGTH_TOKENS: tuple[str, ...] = ("radius",)
FONT_TOKENS: tuple[str, ...] = ("font_sans", "font_mono")
#: 키 이름이 곧 `--primary`이고 그것이 곧 `bg-primary`다 — 매핑 테이블이 없는 것이
#: 이 화이트리스트의 존재 이유다. 임의 키를 받으면 매핑 계층이 생기고, 그 계층은
#: shadcn 버전과 함께 낡는다.
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
    """프로필 정책 위반. `code`가 라우트의 HTTP 상태로 번역된다
    (model_catalog.CatalogError와 같은 계약)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DesignProfile(BaseModel):
    filename: str
    uploaded_at: str
    uploaded_by: str
    markdown: str
    #: 저장물이 아니다 — markdown에서 파싱한 값을 스토어가 채운다.
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
    """(tokens, prose)를 돌려준다. 형식 위반은 DesignProfileError("invalid").

    산문은 펜스 블록만 빼고 나머지 전부다 — 변환하지 않는다. 에이전트가 읽는
    것은 admin이 쓴 문장 그대로여야 한다.
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
    """프로필의 읽기/쓰기. `s3`가 None이면 읽기 전용(버킷 미설정)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> DesignProfile | None:
        """없으면 None. 손상됐어도 None이다. S3가 그 밖의 이유로 실패해도 None이다.

        예외로 올리지 않는 이유는 ModelCatalog가 시드로 떨어지는 것과 같다:
        여기서 raise하면 손으로 편집된 JSON 하나가 **모든 빌드 세션의 start()를
        막는다.** None이면 브랜드 없이 계속 돌고 원인은 로그에 남는다.

        `get` 자체도 넓게 감싼다 — 처음에는 FileNotFoundError(없음)만
        잡았는데, AccessDenied·스로틀·네트워크 오류 같은 그 외 모든 S3 예외가
        그대로 새 나가 이 함수의 약속(없거나 깨졌으면 None)을 어겼다. 이
        스토어가 그 약속을 자기 안에서 지켜야, 호출하는 쪽(session.start(),
        호스팅 라우트)이 저마다 다시 감싸는 것에 의존하지 않아도 된다.
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
        """파싱이 먼저다 — 검증 실패 시 S3에 아무것도 쓰지 않는다."""
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
        """단일 키 delete가 S3StoreLike에 없어서 프리픽스로 지운다 —
        design/ 아래에는 profile.json 하나뿐이다."""
        if self._s3 is None:
            raise DesignProfileError(
                "readonly",
                "design profile is read-only without AIPDS_S3_BUCKET")
        await self._s3.delete_prefix(DESIGN_PREFIX)
