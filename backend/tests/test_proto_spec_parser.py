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
