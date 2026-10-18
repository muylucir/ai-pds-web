# backend/tests/test_proto_spec_parser.py — rules for extracting the card title from a spec document.
from aipds.parsers.proto_spec import MAX_NAME_CHARS, spec_name


def test_path_a1_product_label():
    """The name line of the Path A.1 template (prototype-validation.md:39)."""
    assert spec_name(
        "# Prototype Specification\n"
        "\n"
        "## Derived from Envision\n"
        "- **Product**: 기획전 AI 어시스턴트\n"
        "- **Target User**: 상품 영업 담당자\n"
    ) == "기획전 AI 어시스턴트"


def test_path_b_use_case_label():
    """The name line of the Path B template (prototype-md-format.md:28) — it is not a list item."""
    assert spec_name(
        "# PROTOTYPE-customer-support-agent.md\n"
        "\n"
        "# Customer Support Agent - Prototype Specification\n"
        "\n"
        "**Use Case**: Customer Support Agent\n"
        "**Type**: Agentic\n"
    ) == "Customer Support Agent"


def test_korean_product_label():
    """ko projects write the labels in translation — observed (2026-09-02): all 4 real specs
    in S3 (novadesk, ws-l, test1111, travel-friend) use `- **제품**:`.

    That translation is designed behavior. The ko language convention in
    `agent/workspace_rules.py` instructs that "question wording, headings, **labels**, and
    options are rendered in Korean". A rule that only looks for the English label always
    returns None in ko projects, and then the card falls back to the constant slug
    `"prototype"` — and since ko is the default, that means every project.
    """
    assert spec_name(
        "# 프로토타입 명세\n"
        "\n"
        "## Envision에서 도출\n"
        "- **제품**: FeedSight\n"
        "- **타겟 사용자**: NovaDesk PM (주)\n"
    ) == "FeedSight"


def test_korean_use_case_label():
    """The ko label of the Path B template. There is no observed sample — "Use Case" may
    stay as a technical term (then the existing English pattern catches it), and the same
    convention also instructs translation, so both are accepted. The spelling is not settled
    in this position, so the three common variants are accepted."""
    assert spec_name("**유즈케이스**: 재고 예측\n") == "재고 예측"
    assert spec_name("**유스케이스**: 재고 예측\n") == "재고 예측"
    assert spec_name("**사용 사례**: 재고 예측\n") == "재고 예측"


def test_tagline_after_em_dash_is_dropped():
    """3 of the 4 observed specs take the form `product name — one-line description`. The value
    goes on a single card-title line, so only the name is kept and the description is dropped."""
    assert spec_name(
        "- **제품**: 트래블프렌드 — 여행 상품을 권하지 않는 AI 여행 친구\n"
    ) == "트래블프렌드"


def test_tagline_wrapping_to_the_next_line_does_not_leak():
    """Observed (novadesk): the description wraps onto the next line. A rule that captures only up
    to the end of the line turns the title into a sentence fragment (`… PM이 무엇을`), and
    truncating at 120 characters leaves a fragment as a fragment — the place to cut is not a
    length but the boundary between the name and the description."""
    assert spec_name(
        "- **제품**: NovaDesk Signal — 세 채널에 흩어진 고객 피드백을 근거가 "
        "연결된 테마로 정리해 PM이 무엇을\n"
        "  먼저 만들지 판단할 수 있게 하는 사내 도구 (PR/FAQ 제목)\n"
    ) == "NovaDesk Signal"


def test_no_name_line_at_all():
    """The observed Path B artifact (PROTOTYPE-notam-ai-summary.md) has neither label.
    In that case the caller must fall back to the slug, so the result is None, not an empty string."""
    assert spec_name("# PROTOTYPE-notam-ai-summary\n\n## 1. 개요\n내용\n") is None


def test_unfilled_template_placeholder_is_not_a_name():
    """A document that leaves the template as is. Putting `[From PR/FAQ headline]` up as the card
    title is worse than the slug — the user reads it as not being their prototype."""
    assert spec_name("- **Product**: [From PR/FAQ headline]\n") is None
    assert spec_name("**Use Case**: {Use Case Name}\n") is None


def test_empty_value_is_not_a_name():
    assert spec_name("- **Product**:   \n") is None


def test_first_label_in_document_order_wins():
    """When both labels appear, document order decides — this has to be a deterministic rule
    that does not depend on S3 traversal or dict ordering."""
    assert spec_name("**Use Case**: 뒤엣것\n- **Product**: 앞엣것\n") == "뒤엣것"


def test_trailing_markdown_and_inner_whitespace_are_normalized():
    """It is a one-line card title, so whitespace within the line is collapsed and emphasis markers are stripped."""
    assert spec_name("- **Product**: **재고   예측**  \n") == "재고 예측"


def test_name_is_capped():
    """The model sometimes writes a sentence where the name belongs. The value travels on a single
    card line, so it is cut in the payload — CSS truncate only handles display after it has arrived."""
    name = spec_name("- **Product**: " + "가" * 500 + "\n")
    assert name is not None
    assert len(name) == MAX_NAME_CHARS


def test_label_must_be_a_line_of_its_own():
    """A body line that quotes the label is not read as the name."""
    assert spec_name("설명에서 **Product**: 라는 표기를 쓴다고 적은 문장\n") is None
