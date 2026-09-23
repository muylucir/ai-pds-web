# backend/aipds/preview_surface.py — 프로토타입은 앱과 다른 오리진에서 서빙된다.
#
# **왜 오리진을 나누는가.** 프로토타입은 빌드 에이전트가 쓴 코드이고 신뢰 대상이
# 아니다. 앱과 같은 오리진에서 서빙하면 두 가지가 그 코드에 닿는다:
#
#   - 앱 세션 쿠키(`path=/`)가 프로토타입 요청에도 실린다. Next의 `/api` 프록시가 그
#     쿠키를 `Authorization: Bearer <Cognito access token>`으로 바꿔 붙이므로, 관리자가
#     프리뷰를 열면 관리자 토큰이 프로토타입 서버 프로세스에 들어간다.
#   - 프로토타입의 JS가 같은 오리진이라 `fetch('/api/…')`만으로 보는 사람의 권한으로
#     앱 API를 부를 수 있다.
#
# 헤더를 하나씩 지우는 것은 그 구멍을 막는 것이지 없애는 것이 아니다. 프리뷰 전용
# CloudFront 배포(infra/lib/aipds-preview-stack.ts)가 같은 EC2의 `/api/proto/*`만
# 서빙하고, 앱 오리진은 프로토타입을 서빙하지 않는다 — 앱 쿠키는 앱 도메인에만 있으므로
# 프로토타입에 **구조적으로** 닿지 않는다(`*.cloudfront.net`은 공개 접미사 목록에 있어
# 두 배포는 서로 다른 사이트다).
#
# **요청이 어느 표면으로 왔는지** 백엔드가 아는 방법: 프리뷰 배포만 비밀 헤더
# `X-Preview-Verify`를 붙인다(앱 배포가 붙이는 `X-Origin-Verify`와 별개). nginx와 Next
# 프록시는 이 헤더를 그대로 넘긴다. 값을 모르면 앱 도메인으로 흉내낼 수 없다.
#
# 설정이 없으면(로컬 개발, 프리뷰 스택을 아직 올리지 않은 배포) 예전처럼 앱 오리진이
# 프로토타입을 서빙한다 — 기능이 사라지는 것보다 낫다.
from __future__ import annotations

import logging
import os
import secrets
import time

_log = logging.getLogger(__name__)

ORIGIN_ENV = "AIPDS_PREVIEW_ORIGIN"          # https://dXXXX.cloudfront.net
SECRET_ARN_ENV = "AIPDS_PREVIEW_SECRET_ARN"  # 비밀 헤더 값을 담은 Secrets Manager 시크릿
VERIFY_ENV = "AIPDS_PREVIEW_VERIFY"          # 값을 직접 줄 때(테스트·로컬)
HEADER = "x-preview-verify"


def preview_origin() -> str | None:
    value = os.environ.get(ORIGIN_ENV, "").strip().rstrip("/")
    return value or None


#: 성공한 값만 담는다 — 실패를 캐시하면 기동 직후의 일시적 오류 하나가 재시작 전까지
#: 프리뷰 표면을 꺼 둔다.
_secret_cache: dict[str, str] = {}
#: 실패는 잠깐만 기억한다 — 요청마다 Secrets Manager를 두드리지 않게.
_RETRY_AFTER = 60.0
_failed_at: dict[str, float] = {}


def _secret_from_arn(arn: str) -> str | None:
    if arn in _secret_cache:
        return _secret_cache[arn]
    if time.monotonic() - _failed_at.get(arn, -_RETRY_AFTER) < _RETRY_AFTER:
        return None
    import boto3
    try:
        client = boto3.client("secretsmanager",
                              region_name=os.environ.get("AWS_REGION"))
        value = client.get_secret_value(SecretId=arn)["SecretString"].strip()
        if value:
            _secret_cache[arn] = value
        return value or None
    except Exception:
        # 읽지 못하면 프리뷰 표면을 끈 것과 같다(앱 오리진이 계속 서빙한다). 조용히
        # 넘기면 "왜 링크가 앱 도메인인가"를 알 수 없으므로 남긴다.
        _log.exception("reading the preview verify secret failed")
        _failed_at[arn] = time.monotonic()
        return None


def preview_verify() -> str | None:
    direct = os.environ.get(VERIFY_ENV, "").strip()
    if direct:
        return direct
    arn = os.environ.get(SECRET_ARN_ENV, "").strip()
    return _secret_from_arn(arn) if arn else None


def enabled() -> bool:
    return preview_origin() is not None and preview_verify() is not None


def on_preview_surface(headers) -> bool:
    """이 요청이 프리뷰 배포로 왔는가. 프리뷰가 꺼져 있으면 True(앱 오리진이 서빙한다)."""
    expected = preview_verify()
    if preview_origin() is None or expected is None:
        return True
    presented = headers.get(HEADER, "")
    return bool(presented) and secrets.compare_digest(presented, expected)
