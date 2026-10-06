# backend/aipds/survey/report_labels.py — 설문 리포트 마크다운의 라벨.
#
# 이것은 UI 문구가 아니라 **문서 생성기**다. 리포트는 aiplc-docs/** 아래에
# 산출물로 저장되고 문서 리뷰 화면과 개발자 핸드오프에 들어가므로, UI 언어
# (사용자별 쿠키)가 아니라 프로젝트 언어를 따른다.
#
# error_codes.py의 "백엔드에 번역 시스템을 만들지 않는다"와 모순되지 않는다:
# 그쪽은 사용자에게 보이는 에러 문구이고 UI 언어를 백엔드가 모른다. 여기는
# 문서이고 프로젝트 언어를 백엔드가 이미 안다.
#
# 설문 집계에는 Step 6의 섹션(`## Theme Analysis` 등)을 넣지 않는다. 그 섹션은
# 룰의 `validation-results.md`에 에이전트가 쓴다(proto/layout.SURVEY_AGGREGATE).
# 그래서 집계의 헤딩은 모두 프로젝트 언어다.
from __future__ import annotations

_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"

_LABELS = {
    "ko": {
        "title": "설문 집계",
        "prototype": "프로토타입",
        "survey": "설문",
        "hypothesis": "검증 가설",
        "response_count": "응답 수",
        "survey_status": "설문 상태",
        "collected_at": "취합 시각",
        "status_closed": "마감",
        "status_open": "진행 중",
        "source": "출처",
        "source_name": "AI-PDS 검증 설문 (Survey)",
        "note": ("> AI-PDS가 설문 응답으로 만든 집계다. '결과 취합'을 다시 누르면\n"
                 "> 최신 응답으로 덮어쓴다. 아래는 수집된 데이터만 담는다 — 테마\n"
                 "> 분석·pain point 매핑·빌드 결정은 이 파일을 근거로\n"
                 "> `validation-results.md`에 쓴다(prototype-validation.md Step 6)."),
        "quantitative": "정량 집계",
        "free_text": "자유 응답 전문",
        "mean": "평균",
        "of_5": "/ 5",
        "responses_n": "응답 {n}건",
        "score": "점수",
        "count": "응답 수",
        "option": "선택지",
        "ratio": "비율",
        "free_n": "자유 응답 {n}건 — 전문은 아래 '자유 응답 전문' 참조",
        "no_response": "(응답 없음)",
        "optional_suffix": " (선택)",
        "scale_hint": "1(전혀 아니다) ~ 5(매우 그렇다) 중 선택",
        "free_response": "(자유 응답)",
    },
    "en": {
        "title": "Survey Aggregate",
        "prototype": "Prototype",
        "survey": "Survey",
        "hypothesis": "Validation hypothesis",
        "response_count": "Responses",
        "survey_status": "Survey status",
        "collected_at": "Aggregated at",
        "status_closed": "closed",
        "status_open": "open",
        "source": "Source",
        "source_name": "AI-PDS validation survey (Survey)",
        "note": ("> AI-PDS built this aggregate from the survey responses. Running\n"
                 "> 'Synthesize results' again overwrites it with the latest\n"
                 "> responses. It holds only collected data — theme analysis, pain\n"
                 "> point mapping and the build decision go in\n"
                 "> `validation-results.md`, based on this file\n"
                 "> (prototype-validation.md Step 6)."),
        "quantitative": "Quantitative summary",
        "free_text": "Full free responses",
        "mean": "Mean",
        "of_5": "/ 5",
        "responses_n": "{n} responses",
        "score": "Score",
        "count": "Responses",
        "option": "Option",
        "ratio": "Share",
        "free_n": "{n} free responses — see 'Full free responses' below",
        "no_response": "(no responses)",
        "optional_suffix": " (optional)",
        "scale_hint": "Pick from 1 (not at all) to 5 (very much)",
        "free_response": "(free response)",
    },
}


def labels(language: str) -> dict[str, str]:
    """리포트 라벨 사전. 알 수 없는 언어는 한국어로 떨어진다."""
    return _LABELS[language if language in _LANGUAGES else _DEFAULT]
