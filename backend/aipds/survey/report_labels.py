# backend/aipds/survey/report_labels.py — labels for the survey report markdown.
#
# This is not UI text; it is a **document generator**. The report is saved as an
# artifact under aiplc-docs/** and feeds the document review screen and the developer
# handoff, so it follows the project language, not the UI language (a per-user cookie).
#
# This does not contradict error_codes.py's "do not build a translation system in the
# backend": that one is about user-facing error text, where the backend does not know
# the UI language. This one is a document, and the backend already knows the project language.
#
# The survey rollup does not include the Step 6 sections (`## Theme Analysis` etc.).
# The agent writes those into the rule's `validation-results.md` (proto/layout.SURVEY_AGGREGATE).
# So every heading in the rollup is in the project language.
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
        "started_at": "설문 시작 시각",
        "closed_at": "설문 마감 시각",
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
        "respondents": "응답자 구성",
        "persona": "페르소나",
        "overall": "전체",
        "persona_note": ("> 페르소나 문항의 답으로 다른 문항을 모두 나눴다. 페르소나별 표는\n"
                         "> 응답이 있는 페르소나만 담는다. 자유 응답 앞의 [ ]는 그 응답자의\n"
                         "> 페르소나다."),
    },
    "en": {
        "title": "Survey Aggregate",
        "prototype": "Prototype",
        "survey": "Survey",
        "hypothesis": "Validation hypothesis",
        "response_count": "Responses",
        "survey_status": "Survey status",
        "started_at": "Survey started at",
        "closed_at": "Survey closed at",
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
        "respondents": "Respondents",
        "persona": "Persona",
        "overall": "All",
        "persona_note": ("> Every other question is split by the answer to the persona\n"
                         "> question. Per-persona tables include only personas that\n"
                         "> responded. The [ ] before a free response is that\n"
                         "> respondent's persona."),
    },
}


def labels(language: str) -> dict[str, str]:
    """Report label dictionary. An unknown language falls back to Korean."""
    return _LABELS[language if language in _LANGUAGES else _DEFAULT]
