# 워크샵 자료 포지셔닝 — AI-PLC를 정면으로 차용

날짜: 2026-08-03
대상: `docs/workshop/` 4개 자료 (edm.html, intro.html, pitch-internal.html, facilitator.html)

## 문제

홍보·활용 자료 4종이 만들어졌지만 의도에서 계속 빗나간다. 원인은 자료가
PathFinder의 뿌리인 **AI-PLC**(https://github.com/aws-samples/sample-ai-plc)를
의도적으로 우회했기 때문이다. 실측되는 증거:

| 자료 | AI-PLC 언급 |
|---|---|
| `edm.html` | 0회 |
| `intro.html` | 0회 — 섹션 id는 `#aiplc`인데 제목은 "제품 Discovery"로 익명 처리 |
| `pitch-internal.html` | 1회, 푸터 한 줄 |
| `facilitator.html` | 0회 |

뿌리를 지우면 남는 주장이 **"AI로 프로토타입을 만드는 도구"**뿐이다. 그건
시중에 널려 있어서 "왜 이걸 해야 하는가"에 답하지 못한다. 더 나쁜 것은,
방법론이 규정한 것들(PR/FAQ, 승인 게이트, `PROTOTYPE-*.md`)을 근거 없이
설명해야 해서 자료 곳곳이 **변명하는 톤**이 된다는 점이다.

## 핵심 서사

> AWS는 PM이 코드 이전에 제품을 정의·검증하는 방법론 **AI-PLC**를 공개했다.
> 그런데 그 방법론의 실행 환경은 개발자 도구(Claude Code · 터미널 · 마크다운
> 파일 편집)다 — 정작 대상인 PM이 혼자 돌릴 수 없다. **Pathfinder는 그 간극을
> 메우는 웹 도구**고, 이 워크샵은 PM·기획자가 브라우저만으로 AI-PLC Discovery를
> 반나절에 완주하는 자리다.

관계 규정: **AI-PLC가 주인공, Pathfinder는 실행체.** 고객이 사가는 것은
방법론이고 도구는 수단이다. 방법론이 GitHub에 공개돼 있으므로 "워크샵 후에도
사내에서 이어서 쓸 수 있다"가 근거 있는 약속이 된다.

### 이 서사가 해결하는 것

지금 자료가 각각 따로 변명하던 지점들이 방법론 규정으로 대체된다.

| 지금까지의 약한 지점 | AI-PLC 차용 후 | 상류 근거 |
|---|---|---|
| "왜 PR/FAQ를 쓰나" — 어색함을 달래는 설명 | Envision stage의 규정 산출물. Working Backwards는 AWS 표준 | `discovery/envision.md`, `terminology.md:87` |
| "왜 게이트에서 승인하나" | 룰이 요구하는 승인 지점. PM이 프로세스 오너 | `common/overconfidence-prevention.md` |
| "프로토타입은 납품물이 아닙니다" — 방어적 면책 | Discovery 산출물은 **Inception의 입력**. 라이프사이클상 당연 | `process-overview.md:144-148` |
| "왜 이 도구를 쓰나" | 방법론을 비개발자가 돌릴 수 있게 하는 경로 | `question-format-guide.md:5-6` |
| "워크샵 끝나면 환경 정리됩니다" — 아쉬움 | 방법론은 공개 자산. 사내에서 이어서 쓸 수 있다 | 공개 리포 |
| 확정된 고객에겐 안 맞음 — 우리 판단 | 상류가 Discovery를 **Greenfield only**로 규정 | `terminology.md:8` |

### Pathfinder의 존재 이유 — 대조표

자료에 실을 대조표. 왼쪽이 상류 룰이 PM에게 요구하는 것, 오른쪽이 Pathfinder가
대신하는 것이다.

| AI-PLC 원래 실행 환경 | Pathfinder |
|---|---|
| Claude Code · Kiro 등 AI 코딩 도구 설치·설정 | 브라우저만 |
| 워크스페이스를 Git으로 관리 | 프로젝트가 자동 생성·보관 |
| 질문에 답하려면 `*-questions.md` 파일을 열어 `[Answer]:` 태그 뒤에 입력 | 질문 위저드 폼 |
| 생성된 문서를 파일 탐색기·에디터로 찾아 읽음 | 살아있는 문서 패널 |
| 프로토타입 빌드·호스팅을 직접 구성 | 카드 버튼 → 링크로 열림 |

**단, 왼쪽은 "번거롭다"가 아니라 "비개발자에게는 불가능하다"로 읽히게 쓴다.**
상류 룰 `question-format-guide.md`는 *"Never Ask Questions in Chat — ALL
questions must be placed in dedicated question files"*를 CRITICAL로 규정한다.
즉 PM은 마크다운 파일을 열어 편집해야 한다. 이것이 대조표의 가장 강한 한 줄이다.

## 용어 정합성 결정

상류에 불일치가 있어 자료에서 통일한다.

1. **약어 확장은 "AI-Driven Product Life Cycle"** — 공개 리포 README 기준.
   로컬 룰 `common/terminology.md:205`는 "AI-Driven **Development** Life
   Cycle"이라고 쓰지만, 고객이 링크를 따라가면 리포를 보게 된다.

2. **Phase / Stage 구분을 지킨다** — Discovery는 phase, Envision ·
   Solution Analysis · Prototype & Validation · Product Strategy ·
   Go-to-Market은 stage. 상류가 이 혼용을 명시적으로 금지한다
   (`terminology.md:18-23`). 현재 `intro.html`은 전부 "단계"로 뭉쳐 쓴다.

3. **`PROTOTYPE-*.md`는 상류 파일명 그대로 노출** — 현재 자료는 "프로토타입
   명세"라고만 부른다. 이 파일명이 Entry Point 1(기존 명세로 빌드 직행)의
   전제이므로 이름이 중요하다.

4. **첫 등장 규칙** — 각 자료에서 AI-PLC가 처음 나올 때 반드시 약어를 풀고
   리포 링크를 단다. 이후는 약어만.

## 정직성 제약 — "룰을 그대로 싣는다"고 쓰지 않는다

로컬 룰은 상류를 무수정으로 싣지 않았다.

- `aws-aiplc-rules/core-workflow.md:3`에 한국어 진행 지시가 삽입돼 있다
  (커밋 `e12d806`)
- 모델 ID가 Sonnet 5로 갱신됐다 (커밋 `c806343`)
- 진행 중인 이중언어 작업(`2026-08-03-bilingual-ko-en-design.md`)이 언어 지시를
  프로젝트별로 룰 레벨에 주입하는 구조로 바꾼다

따라서 자료의 표현은 **"방법론 절차는 상류 그대로, 진행 언어만 주입"**이다.
이것은 약점이 아니라 강점으로 쓴다 — 한국 고객이 한국어로 AI-PLC를 돌릴 수 있다.

## 라이프사이클 노출 — 전체 4-phase + 오늘 위치

결정: 4단계를 전부 그리고 Discovery만 하이라이트한다. 근거는
`common/terminology.md:7-11`.

```
  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌────────┐
  │ DISCOVERY│→ │INCEPTION│→ │CONSTRUCT.│→ │  OPS   │
  │  ◀ 오늘  │  │         │  │          │  │        │
  └──────────┘  └─────────┘  └──────────┘  └────────┘
   PM 주도       요구사항      코드 생성      배포·운영
   WHO/WHAT      WHAT/WHY      HOW           DEPLOY/RUN
```

오늘의 산출물(Discovery Document)이 Inception의 입력이 되므로, 프로토타입은
납품물이 아니라 다음 단계로 넘기는 근거다. 이 그림 하나가 워크샵 경계의
정당화와 후속 대화의 개시를 동시에 한다.

노출 범위: `intro.html` · `pitch-internal.html`은 그림, `edm.html`은 이메일
클라이언트 호환을 위해 테이블 4칸 텍스트 버전, `facilitator.html`은 한 줄.

## 자료별 변경 범위

### edm.html — 고객 초대 메일

독자는 아직 신청하지 않은 PM·기획자. 목적은 열고 신청하게 만드는 것. AI-PLC는
**신뢰 근거**로만 쓰고 설명하지 않는다. 제목에는 약어를 넣지 않는다 — 미지의
약어가 제목에 있으면 열람률을 떨어뜨린다.

| 위치 | 변경 |
|---|---|
| 헤더 킥커 (34) | `Pathfinder: From Pain Point to Validated Prototype` → `AWS AI-PLC Discovery · 반일 핸즈온` |
| 제목 (36-41) | 유지 — 고객 언어 |
| 서브 (43) | `Pathfinder 워크샵 · 반일 핸즈온` → `AI-PLC Discovery 워크샵 · Pathfinder로 진행` |
| 🎯 박스 (131-144) | **신뢰 근거를 심는 자리.** 소제목을 `🎯 임의로 만든 커리큘럼이 아닙니다`로. 첫 단락에 풀네임 + 리포 링크 + "AWS가 공개한 방법론" |
| 🎯 박스 마지막 단락 (141) | "이 흐름은 다시 쓸 수 있습니다" → "방법론이 공개돼 있으니 사내에서 이어서 쓸 수 있습니다" |
| 4단계 흐름 (158~) | 각 단계에 stage 이름 병기 — `문제를 정리한다 · Envision` 등 |
| 신규 | 4-phase 라이프사이클 테이블(4칸, 첫 칸 강조) — 🎯 박스 뒤 |
| 푸터 (388) | 리포 URL 명시 |

### intro.html — 참가자 소개자료 ← 변경 최대

방법론을 실제로 가르치는 자료. 지금 §1·§2가 익명 처리한 부분이 전부 실명화된다.

| 섹션 | 변경 |
|---|---|
| hero (299-301) | 킥커를 `AWS AI-PLC · Discovery Phase · Half-Day Hands-on`로. 리드 문단에 풀네임 + 리포 링크 |
| §1 `#why` (311) | 제목 `왜 Discovery인가` → `왜 AI-PLC Discovery인가`. **4-phase 그림 신규 삽입.** 카드 3장 중 3번째(327-329)가 근거 없이 "Inception"을 언급 중 → 그림이 그 근거가 됨 |
| §2 `#aiplc` (339) | 제목 → `AI-PLC Discovery — 세 개의 진입점`. Entry Point 1/2/3, Path A/B, stage 이름을 상류 용어 그대로 병기 |
| §3 `#pathfinder` (418) | **가장 중요한 재작성.** 현재 "방법론을 웹 캔버스로 옮긴 도구" → "AI-PLC의 원래 실행 환경은 개발자 도구다. Pathfinder는 비개발자가 같은 절차를 돌 수 있게 하는 웹 도구다". 위 대조표 신규 삽입 |
| §4 `#tour` (451) | stage 이름 정합화. PR/FAQ 게이트가 Envision stage 산출물임을 명시 |
| §5 `#patha` (521) | Path A가 상류 Entry Point 2임을 명시. 표의 "단계"를 stage로 정정 |
| Working Backwards 노트 (557) | "어색한 게 정상"에 AWS 표준 관행이라는 근거 추가 |
| `PROTOTYPE-*.md` 노트 (563) | 파일명을 상류 표기로. "다른 팀에 넘길 수 있다"가 Entry Point 1임을 연결 |
| §검증 루프 (643) | Prototype & Validation stage임을 명시 |
| 환경 정리 경고 (769) | zip 안내에 "방법론은 공개돼 있어 사내에서 이어서 쓸 수 있다" 추가 |

### pitch-internal.html — SA/AM 내부 공유

AI-PLC가 **재사용 가능한 AWS 자산**이라는 점이 세일즈 논리의 핵심이 된다.

| 위치 | 변경 |
|---|---|
| 헤더 (24-31) | 킥커·서브에 AI-PLC 명시 |
| "우리 입장에서 얻는 것" (70-83) | **3번째 항목 신규** — AWS 공개 방법론이므로 고객에게 남기는 것이 도구 종속이 아니다. 후속 논의가 Inception/Construction으로 이어진다 |
| 신규 | 4-phase 그림 + 후속 단계가 어디로 이어지는지 |
| ⚖️ 포지셔닝 박스 (146-159) | "납품물이 아니다"의 근거를 방법론으로 교체 — Discovery 산출물은 Inception의 입력 |
| "이런 고객에게" 배제 조건 (241-243) | Greenfield only 근거 명시 |
| 신규 | **FAQ 블록** — 아래 3문답 |
| 푸터 (281) | 리포 URL |

FAQ 블록 초안:

- **"리포를 받아서 직접 돌리면 되지 않나요?"**
  돌릴 수 있습니다 — Claude Code나 Kiro를 쓸 줄 아는 사람이라면. 상류 룰은
  질문을 채팅이 아니라 `*-questions.md` 파일에 넣고 `[Answer]:` 태그 뒤에
  답하도록 규정합니다. 워크샵 참가자는 PM·기획자이고, 그들에게 필요한 것은
  방법론이지 터미널이 아닙니다. Pathfinder는 그 간극만 메웁니다.

- **"AI-PLC가 뭔가요?"**
  AWS가 공개한 AI 기반 제품 라이프사이클 방법론입니다
  (`aws-samples/sample-ai-plc`). Discovery → Inception → Construction →
  Operations 4단계이고, 워크샵은 첫 단계인 Discovery를 다룹니다.

- **"Pathfinder는 AWS 제품인가요?"**
  아닙니다. AI-PLC 방법론을 워크샵에서 돌리기 위한 도구입니다. 고객에게 파는
  것은 방법론이고, 도구는 그것을 반나절에 완주하게 하는 수단입니다.

### facilitator.html — 진행 대본

최소 변경. 진행자가 현장에서 질문받았을 때 답할 수 있는 것만.

| 위치 | 변경 |
|---|---|
| hero (249) · §1 요약 (262) | AI-PLC 좌표 한 줄 |
| §단계별 대본 (452) | 각 구간 도입 멘트에 stage 원어 이름 — 참가자 화면에 영어 stage명이 뜨므로 실용적 이득 |
| §게이트 운영 (685) | 게이트가 진행자 재량이 아니라 룰 규정임을 명시 |
| §트러블슈팅 FAQ (728) | 위 3문답 추가 |

## 검증

HTML 정적 자료이므로 자동 테스트가 없다. 대신 각 파일 편집 후 확인한다.

1. **약어 첫 등장 규칙** — 파일별로 `AI-PLC`가 처음 나오는 위치에 풀네임과 리포
   링크가 있는지 grep으로 확인
2. **약어 확장 통일** — `AI-Driven Development Life Cycle`이 자료에 없는지 확인
   (Product로만 나와야 한다)
3. **Phase/Stage 혼용** — stage 이름(Envision, Solution Analysis 등)에 "phase"나
   "단계"가 붙지 않았는지 확인
4. **EDM 호환** — `edm.html`의 신규 라이프사이클 블록은 `<pre>`나 flex/gap 없이
   테이블로만 구성. 기존 파일의 주석(45행)이 같은 제약을 기록하고 있다
5. **브라우저 렌더** — 4개 파일을 열어 레이아웃 붕괴 없는지 육안 확인

## 범위 밖

- 룰 파일(`rule/aiplc-rules/`) 수정 — 상류 동기화 대상이므로 손대지 않는다
- 제품 UI 문자열 — 진행 중인 이중언어 작업의 영역이다
- `README.md` — 개발자 문서이고 이미 AI-PLC를 명시하고 있다
