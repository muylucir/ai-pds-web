# backend/aipds/project_bundle.py — 프로젝트 번들(.zip) 포맷의 단독 소유자.
#
# 한 프로젝트를 다른 인스턴스로 옮기는 파일 하나의 모양을 이 모듈이 정한다.
# export(project_export.py)와 import(project_import.py)가 **같은** 레이아웃
# 상수·제외 규칙·키 변환을 써야 하고, 두 벌이 되면 그중 하나가 드리프트한다.
# `proto/layout.py`가 같은 이유로 존재한다 — 거기서는 "id → 명세 경로"가 네 곳에
# 복제돼 있었고, 규칙이 네 벌이면 그중 하나가 구멍으로 드리프트할 기회도 네 번이다.
#
# 레이아웃:
#
#   export.json                        매니페스트 (아래 build_manifest)
#   project/**                         projects/{pid}/ 서브트리
#   prototypes/{slug}/source/**        로컬 빌드 트리 (S3에 없다 — 아래 참고)
#
# **왜 포함 목록이 아니라 제외 목록인가.** `project/` 는 프로젝트 prefix **전체**를
# 담고 빠지는 것만 열거한다. 포함 목록은 썩는다: 새 서브트리가 생기면(설문이 그랬고
# 승인 이력이 그랬다) 그것을 목록에 넣는 것을 잊은 export가 조용히 불완전한 번들을
# 만들고, 받는 쪽에서는 "그 기능이 없던 프로젝트"와 구별되지 않는다. 제외 목록은
# 반대로 잊으면 **더 담긴다** — 그 실패는 zip이 커지는 것으로 눈에 보인다.
#
# **프로토타입 소스가 S3 밖에 있는 이유.** 인프로세스 빌더가 로컬 빌드 트리에 직접
# 쓰고 ProtoHost가 그 자리에서 서빙한다(routes/prototypes.py의 `_local_build_exists`).
# `prototypes/{slug}/bundle/`은 삭제된 MicroVM 시절의 백업이고 지금 그것을 쓰는
# 코드는 핸드오프 zip의 폴백 하나뿐이다. 그래서 번들은 로컬 디스크를 반드시 읽어야
# 하고, 그 경로는 S3 키와 섞이지 않도록 `prototypes/{slug}/source/` 아래 따로 산다.
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from aipds.pathsafe import reject_unsafe
from aipds.proto.host import TOKEN_FILENAME

#: 번들 포맷의 버전. 임포트는 자기가 아는 버전만 받는다 — 모르는 버전을 최선으로
#: 해석하면 "절반만 들어온 프로젝트"가 되고, 그 상태는 화면에서 정상과 구별되지
#: 않는다. 올릴 때는 `SUPPORTED_SCHEMA_VERSIONS`에 구 버전을 남겨 둔다.
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

#: 이 zip이 무엇인지 자기 서술하는 표식. 사용자가 엉뚱한 zip(프로토타입 핸드오프
#: 아카이브, 산출물 아카이브)을 임포트 칸에 떨어뜨리는 것이 흔한 실수이고,
#: 그때 "export.json이 없다"보다 "이건 프로젝트 번들이 아니다"가 고칠 수 있는 말이다.
KIND = "aipds-project-export"

MANIFEST_NAME = "export.json"
PROJECT_DIR = "project/"
PROTO_DIR = "prototypes/"
PROTO_SOURCE_LEAF = "source/"

#: 프로젝트 매니페스트의 S3 키. 번들에 **넣지 않는다** — 같은 사실(이름·생성일·
#: 모델·언어)이 `export.json`에도 있어야 하는데(사람이 zip을 열어 확인하는 자리이고,
#: 임포트가 프로젝트를 만들기 전에 읽어야 하는 값이다) 두 곳에 두면 어긋난 사본이
#: 생긴다. 임포트는 `write_manifest`로 자기 것을 새로 쓴다.
PROJECT_MANIFEST_KEY = "project.json"

#: pending 질문. 살아 있는 턴에 park된 질문을 새로고침 후 복원하기 위한 편의물이고
#: (agent/pending_store.py), 그 턴은 인메모리 Future다. 임포트한 프로젝트에는 그
#: 턴이 없으므로 복원하면 **아무도 답을 받지 않는 질문 폼**이 뜬다. 번들에 넣고
#: 임포트에서 무시하는 것보다 애초에 담지 않는 편이 정직하다.
PENDING_PREFIX = "pending/"

#: Discovery 트랜스크립트의 루트(프로젝트 상대). 아래 세션 세그먼트 변환을 보라.
TRANSCRIPT_ROOT = "discovery/transcript/"

#: 프로토타입 빌드 에이전트의 트랜스크립트. 대부분 빌드 잡담이고 용량이 크다 —
#: 프로토타입을 **개선**하는 세션의 컨텍스트지만, 임포트한 인스턴스에서는 어차피
#: 새 빌드 세션으로 시작한다(로컬 CLI 트랜스크립트가 없다). 핸드오프 zip이 같은
#: 이유로 같은 것을 뺀다(routes/prototypes.py의 `_ARCHIVE_*` 주석).
_PROTO_TRANSCRIPT_RE = re.compile(r"^prototypes/[^/]+/transcript/")


class BundleError(ValueError):
    """번들이 우리가 아는 모양이 아니다. 라우트가 400으로 번역한다."""


# ---- 프로토타입 소스 트리의 제외 규칙 ----
#
# 개발팀 핸드오프 zip(routes/prototypes.py)과 **같은 집합**이어야 하고, 그래서 그쪽이
# 이 모듈을 임포트한다. 두 벌로 두면 한쪽만 고쳐지고, 이 집합의 실패 모드는 대칭이
# 아니다: 빌드 산출물을 빠뜨리면 zip이 커질 뿐이지만 `.proto-token`을 빠뜨리면
# **살아 있는 접근 자격증명이 다운로드에 실려 나간다**. 그 토큰이 막으려는 대상이
# 바로 그 zip을 받는 사람일 수 있다.
SOURCE_EXCLUDED_DIRS = frozenset({"node_modules", ".next", ".git"})

#: `.proto-host.*`는 이 박스의 부기(로그·pid)이고 다른 인스턴스에서 의미가 없다.
#: `TOKEN_FILENAME`은 리터럴을 복사하지 않고 그 파일명을 소유한 모듈에서 가져온다 —
#: 복사본은 이름이 바뀌는 날 조용히 제외를 멈춘다.
SOURCE_EXCLUDED_FILES = frozenset({
    ".proto-host.log", ".proto-host.pid", TOKEN_FILENAME,
})


def source_excluded(rel: str) -> bool:
    """빌드 트리의 이 상대 경로를 번들에서 빼는가."""
    parts = PurePosixPath(rel).parts
    if any(p in SOURCE_EXCLUDED_DIRS for p in parts):
        return True
    return parts[-1] in SOURCE_EXCLUDED_FILES if parts else True


# ---- 크기 상한 (zip bomb 방어) ----
#
# 임포트는 스테이징 객체를 임시 파일로 내려 `zipfile`로 연다(메모리를 유계로
# 두려고). 그래서 상한이 없으면 압축 폭탄이 먼저 죽이는 것은 프로세스가 아니라
# **인스턴스의 디스크**다 — 그리고 그 인스턴스에는 다른 워크숍 프로젝트의
# 프로토타입 빌드 트리가 함께 산다.
MAX_ENTRIES = 20_000
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def safe_entry_name(name: str) -> str:
    """zip 엔트리 이름을 검증하고 그대로 돌려준다. 위험하면 `BundleError`.

    zip 엔트리 이름은 **아카이브를 만든 사람이 정한 임의의 문자열**이고, 우리는
    그것을 S3 키와 로컬 경로 두 곳에 쓴다. `zipfile.extractall`을 쓰지 않는 이유가
    그것이다 — 그쪽은 알아서 정규화해 주지만, 우리는 엔트리를 하나씩 읽어 각자
    다른 목적지로 보내므로 그 보호가 없다.

    `reject_unsafe`가 절대경로와 `..`를 막고, 여기서 두 가지를 더 막는다.
    윈도우에서 만든 zip의 백슬래시(`a\\..\\b`는 POSIX에서 한 세그먼트로 보이지만
    풀어 쓰는 도구에서는 탈출이다)와 드라이브 문자다.
    """
    if not name or name.endswith("/"):
        raise BundleError(f"unsafe bundle entry: {name!r}")
    if "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise BundleError(f"unsafe bundle entry: {name!r}")
    try:
        reject_unsafe(name)
    except ValueError as e:
        raise BundleError(str(e)) from e
    return name


# ---- S3 키 ↔ 번들 경로 ----
#
# **세션 세그먼트가 이 변환의 존재 이유다.** Discovery 트랜스크립트는
# `discovery/transcript/{uuid5("aipds:"+pid)}/main/NNNNNNNN.jsonl`에 살고
# (agent/session_store.py), 그 uuid는 **프로젝트 id에서 유도된다**. 그러므로 다른
# id로 임포트하면서 키를 그대로 쓰면 히스토리를 읽는 쪽은 아무것도 쓰이지 않은
# prefix를 list하게 되고, `list_history`가 모든 실패를 `[]`로 강등하므로 결과는
# **에러 없는 빈 대화**다. 그 침묵이 이 기능에서 가장 비싼 실패이므로, 번들은
# 세션 세그먼트를 아예 빼고 저장하고 임포트가 대상 프로젝트의 값으로 다시 끼운다.


def s3_key_to_bundle_path(key: str, *, transcript_prefix: str) -> str | None:
    """프로젝트 상대 S3 키 → 번들 내부 경로. 제외 대상이면 None.

    `transcript_prefix`는 이 프로젝트의 **활성** 세션 prefix다
    (`agent/session_store.project_transcript_prefix`).
    """
    if key == PROJECT_MANIFEST_KEY:
        return None
    if key.startswith(PENDING_PREFIX):
        return None
    if _PROTO_TRANSCRIPT_RE.match(key):
        return None
    if key.startswith(TRANSCRIPT_ROOT):
        if not key.startswith(transcript_prefix):
            # 다른 세션 id 아래의 트랜스크립트. 활성 세션이 아니므로 히스토리가
            # 읽지 않는 죽은 데이터이고(load_transcript는 활성 prefix만 본다),
            # 세션 세그먼트를 벗기면 배치 번호가 활성 세션과 **충돌**해 한쪽이
            # 조용히 덮어써진다. 담지 않는 것이 유일하게 안전한 선택이다.
            return None
        return PROJECT_DIR + TRANSCRIPT_ROOT + key[len(transcript_prefix):]
    return PROJECT_DIR + key


def bundle_path_to_s3_key(path: str, *, transcript_prefix: str) -> str | None:
    """번들 내부 경로 → 프로젝트 상대 S3 키. `project/` 밖이면 None.

    `s3_key_to_bundle_path`의 역변환이고, 같은 모듈에 있는 것이 요점이다 —
    세션 세그먼트를 한쪽만 다루면 대화가 사라진다.
    """
    if not path.startswith(PROJECT_DIR):
        return None
    key = path[len(PROJECT_DIR):]
    if not key:
        return None
    if key.startswith(TRANSCRIPT_ROOT):
        return transcript_prefix + key[len(TRANSCRIPT_ROOT):]
    return key


def proto_source_path(slug: str, rel: str) -> str:
    """빌드 트리의 상대 경로 → 번들 내부 경로."""
    return f"{PROTO_DIR}{slug}/{PROTO_SOURCE_LEAF}{rel}"


def parse_proto_source_path(path: str) -> tuple[str, str] | None:
    """번들 내부 경로 → (slug, 빌드 트리 상대 경로). 그 모양이 아니면 None."""
    if not path.startswith(PROTO_DIR):
        return None
    rest = path[len(PROTO_DIR):]
    slug, sep, tail = rest.partition("/")
    if not sep or not slug or not tail.startswith(PROTO_SOURCE_LEAF):
        return None
    rel = tail[len(PROTO_SOURCE_LEAF):]
    return (slug, rel) if rel else None


# ---- 매니페스트 ----


def build_manifest(*, exported_at: str, source_project_id: str,
                   project: dict, counts: dict, prototypes: list[dict]) -> str:
    """`export.json` 본문.

    `excluded`를 함께 싣는다: 받는 쪽이 "왜 이건 안 왔지"를 파일 하나로 답할 수
    있어야 한다. 없는 것을 없다고 말하는 자리다.
    """
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "exported_at": exported_at,
        "source_project_id": source_project_id,
        "project": project,
        "counts": counts,
        "prototypes": prototypes,
        "excluded": [
            *sorted(SOURCE_EXCLUDED_DIRS),
            *sorted(SOURCE_EXCLUDED_FILES),
            PENDING_PREFIX,
            "prototypes/*/transcript/",
        ],
    }, ensure_ascii=False, indent=2)


def parse_manifest(raw: bytes) -> dict:
    """`export.json` → dict. 우리가 아는 모양이 아니면 `BundleError`.

    검사 순서가 메시지의 품질을 정한다: `kind`를 버전보다 먼저 본다 — 엉뚱한 zip을
    떨어뜨린 사용자에게 "지원하지 않는 버전"이라고 말하면 고칠 수 없는 말이 된다.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BundleError(f"{MANIFEST_NAME} is not valid JSON") from e
    if not isinstance(data, dict):
        raise BundleError(f"{MANIFEST_NAME} is not an object")
    if data.get("kind") != KIND:
        raise BundleError(f"not an AI-PDS project bundle (kind={data.get('kind')!r})")
    if data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise BundleError(
            f"unsupported bundle schema_version {data.get('schema_version')!r}")
    project = data.get("project")
    if not isinstance(project, dict):
        raise BundleError(f"{MANIFEST_NAME} has no project metadata")
    source_id = data.get("source_project_id")
    if not isinstance(source_id, str) or not source_id:
        raise BundleError(f"{MANIFEST_NAME} has no source_project_id")
    return data
