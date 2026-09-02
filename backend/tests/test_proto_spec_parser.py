# backend/tests/test_proto_spec_parser.py — 명세 문서에서 카드 제목을 뽑는 규칙.
from aipds.parsers.proto_spec import MAX_NAME_CHARS, spec_name


def test_path_a1_product_label():
    """Path A.1 템플릿(prototype-validation.md:39)의 이름 줄."""
    assert spec_name(
        "# Prototype Specification\n"
        "\n"
        "## Derived from Envision\n"
        "- **Product**: 기획전 AI 어시스턴트\n"
        "- **Target User**: 상품 영업 담당자\n"
    ) == "기획전 AI 어시스턴트"


def test_path_b_use_case_label():
    """Path B 템플릿(prototype-md-format.md:28)의 이름 줄 — 목록 항목이 아니다."""
    assert spec_name(
        "# PROTOTYPE-customer-support-agent.md\n"
        "\n"
        "# Customer Support Agent - Prototype Specification\n"
        "\n"
        "**Use Case**: Customer Support Agent\n"
        "**Type**: Agentic\n"
    ) == "Customer Support Agent"


def test_korean_product_label():
    """ko 프로젝트는 라벨을 번역해서 쓴다 — 실측(2026-09-02): S3에 있는 실제 명세
    4개(novadesk, novadesk-2, test1111, travel-friend)가 모두 `- **제품**:`이다.

    그 번역은 설계된 동작이다. `agent/workspace_rules.py`의 ko 언어 규약이
    "질문 문구·헤딩·**라벨**·선택지는 한국어로 옮긴다"고 지시한다. 영어 라벨만
    보는 규칙은 ko 프로젝트에서 항상 None을 돌려주고, 그러면 카드는 상수
    슬러그 `"prototype"`으로 되돌아간다 — ko가 기본값이므로 그것이 곧 전부다.
    """
    assert spec_name(
        "# 프로토타입 명세\n"
        "\n"
        "## Envision에서 도출\n"
        "- **제품**: FeedSight\n"
        "- **타겟 사용자**: NovaDesk PM (주)\n"
    ) == "FeedSight"


def test_korean_use_case_label():
    """Path B 템플릿의 ko 라벨. 실측 표본은 없다 — "Use Case"는 기술용어로 남을
    수도 있고(그러면 기존 영어 패턴이 받는다), 같은 규약이 번역도 지시하므로
    양쪽을 다 받는다. 표기가 하나로 정해지지 않는 자리라 흔한 세 판을 받는다."""
    assert spec_name("**유즈케이스**: 재고 예측\n") == "재고 예측"
    assert spec_name("**유스케이스**: 재고 예측\n") == "재고 예측"
    assert spec_name("**사용 사례**: 재고 예측\n") == "재고 예측"


def test_tagline_after_em_dash_is_dropped():
    """실측 4개 중 3개가 `제품명 — 한 줄 설명` 형태다. 이 값은 카드 제목 한 줄에
    실리므로 이름만 남기고 설명은 버린다."""
    assert spec_name(
        "- **제품**: 트래블프렌드 — 여행 상품을 권하지 않는 AI 여행 친구\n"
    ) == "트래블프렌드"


def test_tagline_wrapping_to_the_next_line_does_not_leak():
    """실측(novadesk): 그 설명이 다음 줄로 이어진다. 줄 끝까지만 잡는 규칙으로는
    제목이 문장 조각(`… PM이 무엇을`)이 되고, 120자 절단은 조각을 조각으로
    남긴다 — 자를 자리는 길이가 아니라 이름과 설명의 경계다."""
    assert spec_name(
        "- **제품**: NovaDesk Signal — 세 채널에 흩어진 고객 피드백을 근거가 "
        "연결된 테마로 정리해 PM이 무엇을\n"
        "  먼저 만들지 판단할 수 있게 하는 사내 도구 (PR/FAQ 제목)\n"
    ) == "NovaDesk Signal"


def test_no_name_line_at_all():
    """실측 Path B 산출물(PROTOTYPE-notam-ai-summary.md)에는 두 라벨이 다 없다.
    그때는 호출부가 슬러그로 되돌아가야 하므로 None이지, 빈 문자열이 아니다."""
    assert spec_name("# PROTOTYPE-notam-ai-summary\n\n## 1. 개요\n내용\n") is None


def test_unfilled_template_placeholder_is_not_a_name():
    """템플릿을 그대로 남긴 문서. `[From PR/FAQ headline]`을 카드 제목으로 걸면
    슬러그보다 나쁘다 — 사용자가 자기 프로토타입이 아니라고 읽는다."""
    assert spec_name("- **Product**: [From PR/FAQ headline]\n") is None
    assert spec_name("**Use Case**: {Use Case Name}\n") is None


def test_empty_value_is_not_a_name():
    assert spec_name("- **Product**:   \n") is None


def test_first_label_in_document_order_wins():
    """두 라벨이 함께 나오면 문서 순서로 정한다 — s3 순회나 dict 순서에
    의존하지 않는 결정적 규칙이어야 한다."""
    assert spec_name("**Use Case**: 뒤엣것\n- **Product**: 앞엣것\n") == "뒤엣것"


def test_trailing_markdown_and_inner_whitespace_are_normalized():
    """한 줄 카드 제목이므로 줄 안의 공백은 접고 강조 기호는 벗긴다."""
    assert spec_name("- **Product**: **재고   예측**  \n") == "재고 예측"


def test_name_is_capped():
    """모델이 이름 자리에 문장을 쓰는 일이 있다. 카드 한 줄에 실려 오는 값이므로
    페이로드에서 자른다 — CSS truncate는 이미 넘어온 뒤의 표시 문제만 다룬다."""
    name = spec_name("- **Product**: " + "가" * 500 + "\n")
    assert name is not None
    assert len(name) == MAX_NAME_CHARS


def test_label_must_be_a_line_of_its_own():
    """본문이 라벨을 인용하는 줄을 이름으로 읽지 않는다."""
    assert spec_name("설명에서 **Product**: 라는 표기를 쓴다고 적은 문장\n") is None
