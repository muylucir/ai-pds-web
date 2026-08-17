<!-- pathfinder-tool-encoding -->
# Tool-parameter encoding (applies to every tool call, in any language)

Write non-ASCII text — Korean included — in tool-call parameters as **literal
UTF-8 characters**. Never as `\uXXXX` unicode escapes.

This is an encoding rule, not a language rule: it says nothing about which
language to write in, only that whatever language you write must reach the tool
as real characters.

Why it is worth stating this bluntly: hand-spelling four hex digits per syllable
mis-spells some of them, and a mis-spelled codepoint decodes to a *different,
valid-looking* syllable. The question then reads as nonsense to the user and no
longer matches the question file it was written into, so their answer cannot be
recorded against it.

# 언어 규약 (이 문서 전체의 전제)

**모든 대화, 문서작성, 질의 응답은 한국어로 진행한다.** 단 기술용어·고유명사·
파일명은 영어를 그대로 유지한다.

## 아래 워크플로우 양식의 영어 문구는 번역해서 쓴다

이 문서 뒤에 오는 워크플로우와 `aws-aiplc-rule-details/`의 문서 양식에는
**완성된 영어 문장**이 리터럴로 박혀 있다. 대표적으로 `envision.md`의 PR/FAQ
질문들이다:

```markdown
#### Q: What is the price?
A: [Answer]
```

`A:` 쪽은 `[Answer]`라는 빈 자리지만 `Q:` 쪽은 이미 영어로 완성돼 있어서, 그대로
복사하면 **질문은 영어, 답변은 한국어**인 문서가 나온다. 실제로 그렇게 나왔다:
템플릿에 있던 질문 20여 개는 영어로 남고, 에이전트가 직접 추가한 질문 하나만
한국어였다.

원인은 두 지시가 반대를 말하기 때문이다 — 위의 "모든 문서작성은 한국어"와,
템플릿 바로 앞의 `**CRITICAL**: Use the ... format exactly as defined below.
Do NOT deviate from this structure.` **그 CRITICAL은 이렇게 읽어야 한다:**

- **"exactly as defined"가 요구하는 것은 구조다** — 섹션 순서, 항목 구성, 어느
  질문이 들어가는지, 계층(`####`)과 `Q:`/`A:` 표기. 이것은 바꾸지 않는다.
- **언어는 구조가 아니다.** 질문 문구·헤딩·라벨은 **한국어로 번역해서 쓴다.**
  질문을 빼거나 순서를 바꾸거나 새로 만들라는 뜻이 아니다 — 같은 질문을 한국어로
  적으라는 뜻이다.

적용 대상은 PR/FAQ만이 아니다. `product-strategy.md`, `go-to-market.md`에도 같은
형태의 영어 리터럴이 있고(각각 십수 개), 같은 규칙을 적용한다. 즉 **양식에서
가져온 모든 사용자 노출 문구는 한국어로 옮긴다.**

영어를 그대로 두는 것은 위에서 예외로 둔 것뿐이다 — **기술용어·고유명사·
파일명**, 그리고 경로·도구 이름·코드 식별자. 예를 들어 `PROTOTYPE-{slug}.md`,
`offline-first`, `TAM`, `SaaS`는 그대로 두고, `Q: What is the price?`는
`Q: 가격은 어떻게 책정되나요?`로 적는다.

문서의 **섹션 헤딩도 같다**(`### Press Release` → `### 보도자료`,
`### External FAQs (Customer-Facing)` → `### 외부 FAQ (고객 대상)`). 단
`submit_document`가 파싱에 의존하는 파일명과 경로는 절대 번역하지 않는다.

## 분량은 감이 아니라 기준으로 맞춘다

<!-- depth-bar-language-clause -->

**분량을 감으로 조절하지 마라.** 같은 내용이라도 언어마다 토큰 비용이 다르기
때문에(한국어는 문자당 영어의 약 3배), "적당한 길이"라는 감각을 그대로 따르면
문서의 깊이가 과제가 아니라 **언어에 따라** 달라진다. 2026-08-13 실측: 같은
스테이지를 언어만 바꿔 돌렸을 때 섹션 수와 질문 수는 같은데 필드별 밀도가
갈렸고, 두 문서 모두 완전성 검사는 통과했다.

**무엇을 얼마나 깊이 쓰는가**의 기준은 언어와 무관하므로 이 파일에 두지 않는다.
공유 config의 `CLAUDE.md`("Depth of what you write" 절)가 그 기준이고, 어느
언어로 쓰든 그대로 적용된다 — 그 기준을 이 문서의 언어 규약과 같은 무게로
읽어라.

---


# DISCOVERY PHASE WORKFLOW (Product Manager Workspace)

## Purpose
This workspace is exclusively for Product Managers to complete the Discovery phase. The output is a comprehensive Discovery Document that will be provided to developers for Inception and Construction phases in a separate workspace.

## Workflow Principle
**The workflow adapts to the work, not the other way around.**

The AI model intelligently assesses which Discovery stages are needed based on:
1. Whether PROTOTYPE-*.md files already exist
2. Whether user wants to start from pain points or use cases
3. Number of use cases to evaluate
4. User's goals (handoff files vs build prototypes)

## MANDATORY: Rule Details Loading
**CRITICAL**: When performing any phase, you MUST read and use relevant content from rule detail files.

Rule details location: `./aws-aiplc-rule-details/`

**Common Rules**: ALWAYS load common rules at workflow start:
- Load `common/process-overview.md` for workflow overview
- Load `common/session-continuity.md` for session resumption guidance
- Load `common/content-validation.md` for content validation requirements
- Load `common/question-format-guide.md` for question formatting rules
- Reference these throughout the workflow execution

## MANDATORY: Content Validation
**CRITICAL**: Before creating ANY file, you MUST validate content according to `common/content-validation.md` rules:
- Validate Mermaid diagram syntax
- Validate ASCII art diagrams (see `common/ascii-diagram-standards.md`)
- Escape special characters properly
- Provide text alternatives for complex visual content
- Test content parsing compatibility

## MANDATORY: Question File Format
**CRITICAL**: When asking questions at any phase, you MUST follow question format guidelines.

**See `common/question-format-guide.md` for complete question formatting rules including**:
- Multiple choice format (A, B, C, D, E options)
- [Answer]: tag usage
- Answer validation and ambiguity resolution

## MANDATORY: Custom Welcome Message
**CRITICAL**: When starting ANY Discovery workflow, you MUST display the welcome message.

**How to Display Welcome Message**:
1. Load the welcome message from `common/welcome-message.md`
2. Display the complete message to the user
3. This should only be done ONCE at the start of a new workflow
4. Do NOT load this file in subsequent interactions to save context space

---

# DISCOVERY PHASE WORKFLOW

## Overview

The Discovery Phase has **THREE ENTRY POINTS**:

1. **Entry Point 1 (Highest Priority)**: Existing PROTOTYPE-*.md files → Build prototypes directly
2. **Entry Point 2 (Path A)**: Start from pain points → PR/FAQ → Solution Analysis
3. **Entry Point 3 (Path B)**: Start from use cases → Prioritization → Prototype Context Generation

---

## Workspace Detection (ALWAYS EXECUTE FIRST)

1. **MANDATORY**: Log initial user request in audit.md with complete raw input
2. Load all steps from `inception/workspace-detection.md`
3. Execute workspace detection:
   - **PRIORITY CHECK**: Check for existing PROTOTYPE-*.md files (Entry Point 1)
     - Path: `aiplc-docs/discovery/prototypes/*/PROTOTYPE-*.md`
     - If found: Skip all discovery, jump to Prototype Building
   - Check for existing aiplc-state.md (resume if found)
   - Check for existing Discovery artifacts
4. Determine next phase:
   - If PROTOTYPE-*.md found: Prototype Building (Entry Point 1)
   - If no PROTOTYPE-*.md: Discovery Mode Selection (Entry Point 2 or 3)
5. **MANDATORY**: Log findings in audit.md
6. Present completion message to user
7. Automatically proceed to next phase

---

## ENTRY POINT 1: Existing PROTOTYPE-*.md Files

**Execute IF**: Workspace Detection finds PROTOTYPE-*.md files

**Purpose**: Build prototypes from pre-generated specifications (typical workshop scenario)

**Flow**:
1. Announce found PROTOTYPE-*.md files
2. **SKIP ALL DISCOVERY PHASES**
3. **JUMP TO**: Prototype Building (see below)
4. After prototypes built, continue to Product Strategy → GTM

---

## ENTRY POINT 2 & 3: Discovery Mode Selection

**Execute IF**: No PROTOTYPE-*.md files found

Load `discovery/discovery-mode-selection.md`

Ask user:
```
How would you like to start Discovery?

[A] Start from customer pain points (create PR/FAQ)
[B] I already have use cases to prioritize

[Answer]:
```

- If [A]: Proceed to **PATH A - Envision**
- If [B]: Proceed to **PATH B - Use Case Intake**

---

## PATH A: Start from Pain Points

### Step 1: Envision

Load `discovery/envision.md`

**Execution**:
1. **MANDATORY**: Log start of Envision in audit.md
2. Determine pain point gathering mode (interactive or URL-based)
3. If URL mode: read ONLY the user-provided URL
4. Gather and confirm customer pain points
5. Generate PR/FAQ document using Working Backwards format
6. **Wait for Explicit Approval**: Present PR/FAQ for review
7. **MANDATORY**: Log user's response in audit.md

### Step 2: Solution Analysis

Load `discovery/solution-analysis.md`

**Execution**:
1. Analyze PR/FAQ to identify solutions
2. Determine: Single solution OR Multiple solutions

**Branch A.1: Single Solution**
- One clear solution from PR/FAQ
- Proceed to Prototype & Validation (single prototype flow)
- Load `discovery/prototype-validation.md`
- Build prototype, iterate, validate
- Continue to Product Strategy → GTM

**Branch A.2: Multiple Solutions**
- PR/FAQ suggests N different solution options
- Extract use cases from PR/FAQ
- **MERGE WITH PATH B** at Use Case Prioritization

---

## PATH B: Start from Use Cases

### Step 1: Use Case Intake

Load `discovery/use-case-intake.md`

**Execution**:
1. **MANDATORY**: Log start in audit.md
2. Ask: "How many use cases?" (could be 3, 5, 10, N)
3. Gather details for all N use cases
4. Categorize: Agentic vs Application
5. Document in `aiplc-docs/discovery/use-case-intake/use-cases.md`

### Step 2: Use Case Prioritization

Load `discovery/use-case-prioritization.md`

**Execution**:
1. Apply prioritization frameworks:
   - Agentic framework for agentic use cases
   - Application framework for application use cases
2. Generate scores for all N use cases
3. Present ranked list
4. User confirms or adjusts
5. Select top 3 for prototyping
6. Document in `aiplc-docs/discovery/prioritization/`

### Step 3: Prototype Context Generation

Load `discovery/prototype-context-generation.md`

**Execution**:
1. For each of top 3 use cases:
   - Gather design context (brand URL)
   - Define requirements (LLM, tools, features)
   - Specify frontend (device, screens)
   - Generate PROTOTYPE-{use-case-slug}.md file
2. Create 3 PROTOTYPE-*.md files in `aiplc-docs/discovery/prototypes/`

### Step 4: Decision Point

Ask user:
```
What would you like to do next?

[A] Build all 3 prototypes now (in this session)
[B] Stop here - hand off PROTOTYPE-*.md files to teams
[C] Build only specific prototypes

[Answer]:
```

**If [A] - Build now**: Proceed to Prototype Building
**If [B] - Hand off**: End Discovery, optionally continue to Product Strategy
**If [C] - Build specific**: Ask which ones, proceed to Prototype Building

---

## Prototype Building (All Paths Converge Here)

Load `discovery/prototype-building.md`

**Execute IF**:
- Entry Point 1: PROTOTYPE-*.md files found
- Path A.1: Single solution from PR/FAQ
- Path A.2 or Path B: User chose to build prototypes

**Execution**:
1. For each PROTOTYPE-*.md file:
   - Read specification
   - Show defaults, ask for LLM provider (ALWAYS ask)
   - Check API credentials
   - Request permission to build
   - Activate Strands Power (if agentic)
   - Build prototype
   - Deploy locally
   - User validates and iterates
2. All prototypes complete

### Selection (if multiple prototypes)

If multiple prototypes built, ask user to select winner for Product Strategy

---

## Product Strategy

Load `discovery/product-strategy.md`

**Execute**: For selected use case

**Execution**:
1. Capture positioning and differentiation
2. Define business model
3. Document strategy decisions
4. **Wait for Explicit Approval**

---

## Go-to-Market

Load `discovery/go-to-market.md`

**Execute**: For selected use case

**Execution**:
1. Marketing strategy
2. Sales approach
3. Launch planning
4. **Wait for Explicit Approval**

---

## Discovery Complete

**Output**: `aiplc-docs/discovery/discovery-document.md`

This comprehensive document contains:
- Pain points and PR/FAQ (if Path A)
- Use cases and prioritization (if Path B)
- Prototype specifications (PROTOTYPE-*.md files)
- Product Strategy
- Go-to-Market plan

**Next Steps for Developers**:
This Discovery Document will be provided to developers in a separate workspace for:
- Inception Phase (Requirements Analysis, Workflow Planning, etc.)
- Construction Phase (Code Generation, Build & Test)
- Operations Phase (Deployment, Monitoring)

---

## Key Principles

- **PM-Focused**: This workspace is for Product Managers only
- **Discovery Only**: No Inception, Construction, or Operations phases
- **Three Entry Points**: Flexible starting points based on situation
- **Workshop-Friendly**: PROTOTYPE-*.md files are portable and shareable
- **Scalable**: Supports any number of use cases (N)
- **Transparent**: Always show defaults, ask for LLM provider
- **Standard AI-PLC**: Uses [Answer]: format throughout
- **Handoff-Ready**: Can stop after generating PROTOTYPE-*.md files
- **Complete Output**: Discovery Document ready for developers

## Audit Logging

**MANDATORY**: Log ALL user inputs and AI responses in `aiplc-docs/audit.md`:
- Capture user's COMPLETE RAW INPUT exactly as provided
- Never summarize or paraphrase user input
- Log every interaction with timestamp
- Use ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)

**NEVER log the following in audit.md:**
- API keys, tokens, or secrets of any kind
- AWS credentials (access keys, secret keys, bearer tokens)
- Any value that appears to be a credential (strings starting with `AKIA`, `sk-`, `bedrock-api-key-`, `goog_`, etc.)
- If a user accidentally pastes a credential in chat or an answer file, redact it before logging — replace with `[CREDENTIAL REDACTED]`
- Log only "credentials configured: yes/no" — never the actual values

## Directory Structure

```
aiplc-docs/
├── discovery/
│   ├── discovery-document.md          # Main output
│   ├── envision/                      # Path A
│   │   ├── pain-points.md
│   │   └── prfaq.md
│   ├── solution-analysis/             # Path A (if multiple)
│   │   └── identified-solutions.md
│   ├── use-case-intake/               # Path B or Path A.2
│   │   └── use-cases.md
│   ├── prioritization/                # Path B or Path A.2
│   │   ├── framework.md
│   │   ├── scoring.md
│   │   └── ranking.md
│   ├── prototypes/                    # All paths
│   │   ├── {use-case-1-slug}/
│   │   │   ├── PROTOTYPE-{use-case-1-slug}.md  ★ Shareable
│   │   │   ├── design-context.md
│   │   │   └── iteration-log.md
│   │   ├── {use-case-2-slug}/
│   │   │   └── PROTOTYPE-{use-case-2-slug}.md  ★ Shareable
│   │   └── {use-case-3-slug}/
│   │       └── PROTOTYPE-{use-case-3-slug}.md  ★ Shareable
│   ├── product-strategy/
│   │   └── strategy.md
│   └── go-to-market/
│       └── gtm-plan.md
├── aiplc-state.md
└── audit.md
```

## Success Criteria

✅ Discovery Document complete with all sections
✅ PROTOTYPE-*.md files generated (if applicable)
✅ Product Strategy documented
✅ Go-to-Market plan documented
✅ All artifacts ready for developer handoff
✅ Complete audit trail in audit.md

## Handoff to Developers

When Discovery is complete, provide developers with:
1. Complete `aiplc-docs/discovery/` directory
2. Especially `discovery-document.md` (main artifact)
3. All PROTOTYPE-*.md files (if generated)
4. Product Strategy and GTM documents

Developers will use these in their workspace for Inception and Construction phases.
