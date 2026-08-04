# 워크샵 자료 AI-PLC 포지셔닝 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/workshop/` 4개 HTML 자료를 AI-PLC 방법론이 주인공이고 Pathfinder가 실행체라는 서사로 재작성한다.

**Architecture:** 순수 정적 HTML 편집이다. 코드도 빌드도 테스트 러너도 없다. 각 태스크는 파일 하나를 편집하고 `grep` 기반 검증 명령으로 확인한 뒤 커밋한다. `edm.html`은 이메일 클라이언트 제약(테이블 레이아웃, 인라인 CSS, flex/gap 금지)을 지키고, 나머지 3개는 기존 CSS 클래스만 재사용해 새 CSS를 추가하지 않는다.

**Tech Stack:** HTML + 인라인 CSS(edm) / `<style>` 블록 CSS 클래스(intro·facilitator·pitch). 편집 도구는 Edit. 검증은 `grep`.

**Spec:** `docs/superpowers/specs/2026-08-03-aiplc-positioning-design.md`

## Global Constraints

이 절의 규칙은 **모든 태스크에 암묵적으로 포함**된다. 태스크마다 다시 확인할 것.

- **약어 확장은 반드시 `AI-Driven Product Life Cycle`.** `AI-Driven Development Life Cycle`은 어떤 자료에도 쓰지 않는다. (로컬 룰 `terminology.md:205`가 Development라고 쓰지만 공개 리포 README가 Product이고, 고객은 리포를 본다.)
- **리포 URL은 정확히 `https://github.com/aws-samples/sample-ai-plc`**
- **첫 등장 규칙**: 각 파일에서 `AI-PLC`가 처음 나오는 곳에 풀네임과 리포 링크를 함께 단다. 그 뒤로는 약어만 쓴다.
- **Phase / Stage 구분**: `Discovery`·`Inception`·`Construction`·`Operations`는 phase(단계). `Envision`·`Solution Analysis`·`Prototype & Validation`·`Product Strategy`·`Go-to-Market`은 stage. stage 이름 뒤에 "단계"나 "phase"를 붙이지 않는다.
- **stage 이름은 영문 그대로 표기**한다(참가자 화면에 영문 stage명이 뜨므로 대조 가능해야 한다). 한글 설명을 병기할 때는 `Envision(페인 포인트 · PR/FAQ)` 형태.
- **`PROTOTYPE-*.md`는 상류 파일명 그대로 노출**한다. "프로토타입 명세"라고만 부르지 않는다.
- **"룰을 그대로/무수정으로 싣는다"고 쓰지 않는다.** 허용 표현은 "방법론 절차는 상류 그대로, 진행 언어만 주입". (로컬 룰은 `core-workflow.md:3`에 한국어 지시가 삽입돼 있고 모델 ID도 갱신됐다.)
- **Discovery는 Greenfield 전용**이라는 상류 규정(`terminology.md:8`)을 배제 조건의 근거로 쓴다.
- **`edm.html` 전용**: `<pre>`, `flex`, `gap`, `grid` 금지. 레이아웃은 `<table>`, 스타일은 인라인 `style=`만. (파일 45행 주석이 같은 제약을 기록한다.)
- **intro/facilitator/pitch**: 새 CSS 규칙을 `<style>`에 추가하지 않는다. 기존 클래스만 재사용한다.
- 한글은 리터럴 UTF-8로 쓴다. `\uXXXX` 이스케이프 금지.

## 4-phase 라이프사이클 — 자료 공통 문안

세 자료에 들어가는 같은 내용의 서로 다른 마크업이다. 문안은 통일한다.

- 제목: `AI-PLC — AI-Driven Product Life Cycle`
- 4칸: `DISCOVERY` / `INCEPTION` / `CONSTRUCTION` / `OPERATIONS`
- 각 칸 부제: `PM 주도 · 문제 정의` / `요구사항 · 워크플로` / `설계 · 코드 · 테스트` / `배포 · 운영`
- 각 칸 초점: `WHO · WHAT` / `WHAT · WHY` / `HOW` / `DEPLOY · RUN`
- Discovery 칸에 `◀ 오늘` 표시
- 캡션: `오늘의 산출물(Discovery Document)이 Inception의 입력이 됩니다 — 그래서 프로토타입은 납품물이 아니라 다음 단계로 넘기는 근거입니다.`

## 고객 FAQ 3문답 — 자료 공통 문안

`pitch-internal.html`(SA/AM용)과 `facilitator.html`(진행자용)에 같은 내용이 들어간다. 문안은 통일한다.

**Q1. "리포를 받아서 직접 돌리면 되지 않나요?"**
> 돌릴 수 있습니다 — Claude Code나 Kiro를 쓸 줄 아는 사람이라면. 상류 룰은 질문을 채팅이 아니라 `*-questions.md` 파일에 넣고 `[Answer]:` 태그 뒤에 답하도록 규정합니다. 워크샵 참가자는 PM·기획자이고, 그들에게 필요한 것은 방법론이지 터미널이 아닙니다. Pathfinder는 그 간극만 메웁니다.

**Q2. "AI-PLC가 뭔가요?"**
> AWS가 공개한 AI 기반 제품 라이프사이클 방법론입니다(`aws-samples/sample-ai-plc`). Discovery → Inception → Construction → Operations 4단계이고, 워크샵은 첫 단계인 Discovery를 다룹니다.

**Q3. "Pathfinder는 AWS 제품인가요?"**
> 아닙니다. AI-PLC 방법론을 워크샵에서 돌리기 위한 도구입니다. 고객에게 파는 것은 방법론이고, 도구는 그것을 반나절에 완주하게 하는 수단입니다.

## File Structure

| 파일 | 책임 | 태스크 |
|---|---|---|
| `docs/workshop/intro.html` | 참가자 소개자료. 방법론을 가르친다. 변경 최대 | 1, 2, 3 |
| `docs/workshop/edm.html` | 고객 초대 메일. AI-PLC를 신뢰 근거로만 쓴다 | 4 |
| `docs/workshop/pitch-internal.html` | SA/AM 내부. AI-PLC를 재사용 가능한 AWS 자산으로 판다 | 5 |
| `docs/workshop/facilitator.html` | 진행 대본. 현장 질문 대응만 | 6 |
| — | 4개 파일 교차 검증 | 7 |

`intro.html`을 3개 태스크로 나누는 이유: 이 파일 하나에 스펙의 핵심 재작성(§3 Pathfinder 정체성)과 용어 정합화가 모두 걸려 있어 한 태스크로 묶으면 리뷰 단위가 너무 커진다. 태스크 1(뼈대·라이프사이클), 태스크 2(§3 재작성 — 가장 중요), 태스크 3(stage 용어 정합화)으로 나눠 각각 독립적으로 거부/승인될 수 있게 한다.

---

### Task 1: intro.html — 네비·hero·§1·§2 실명화 + 라이프사이클 그림

`intro.html`의 상단(네비, hero)과 §1 `#why`, §2 `#aiplc`를 AI-PLC 실명으로 바꾸고, 4-phase 라이프사이클 그림을 §1에 삽입한다.

**Files:**
- Modify: `docs/workshop/intro.html:281` (네비 브랜드 태그)
- Modify: `docs/workshop/intro.html:283-284` (네비 메뉴 1·2번)
- Modify: `docs/workshop/intro.html:299-301` (hero 킥커·리드)
- Modify: `docs/workshop/intro.html:312` (§1 제목)
- Modify: `docs/workshop/intro.html:315` (§1 Discovery 정의 문단)
- Modify: `docs/workshop/intro.html:327-329` (§1 카드 3번)
- Modify: `docs/workshop/intro.html:336` 직전 (라이프사이클 그림 신규 삽입)
- Modify: `docs/workshop/intro.html:340-341` (§2 제목·리드)

**Interfaces:**
- Produces: 라이프사이클 그림의 마크업 패턴(`.cards.c4` + `.card` 재사용). Task 5가 `pitch-internal.html`에서 같은 내용을 테이블 마크업으로 다시 만든다 — 문안은 이 태스크에서 확정한 것을 그대로 쓴다.
- Produces: hero 킥커 문구 `AWS AI-PLC · Discovery Phase · Half-Day Hands-on`. Task 6이 `facilitator.html` 킥커를 이와 정합하게 맞춘다.

- [ ] **Step 1: 네비 브랜드 태그와 메뉴 실명화**

`281`행의 브랜드 태그에서 영문 슬로건을 AI-PLC 좌표로 교체한다.

찾을 문자열:
```html
<span class="tag">From Pain Point to Validated Prototype · 반일 핸즈온</span>
```
바꿀 문자열:
```html
<span class="tag">AWS AI-PLC Discovery · 반일 핸즈온</span>
```

`283-284`행 메뉴 두 줄:
```html
        <a href="#why"><span class="n">1</span>왜 Discovery인가</a>
        <a href="#aiplc"><span class="n">2</span>Discovery 경로</a>
```
바꿀 문자열:
```html
        <a href="#why"><span class="n">1</span>왜 AI-PLC인가</a>
        <a href="#aiplc"><span class="n">2</span>세 개의 진입점</a>
```

- [ ] **Step 2: hero 킥커와 리드 문단에 풀네임·리포 링크**

`299-301`행을 교체한다. 이 파일의 AI-PLC 첫 등장이므로 **풀네임과 리포 링크가 여기 들어간다.**

찾을 문자열:
```html
      <p class="kicker">Pathfinder: From Pain Point to Validated Prototype · Half-Day Hands-on</p>
      <h1><span class="gr">고객의 문제에서 시작해</span><br>만질 수 있는 프로토타입까지</h1>
      <p>오늘 우리는 <b>Pathfinder</b>로 제품 Discovery를 한 바퀴 돕니다. 고객의 페인 포인트를 모으고, PR/FAQ로 구조화하고, 프로토타입 명세를 만들고 — 그 명세를 <b>실제로 도는 앱</b>으로 빌드해 사용자에게 검증까지 받습니다. 슬라이드가 아니라 화면에서.</p>
```
바꿀 문자열:
```html
      <p class="kicker">AWS AI-PLC · Discovery Phase · Half-Day Hands-on</p>
      <h1><span class="gr">고객의 문제에서 시작해</span><br>만질 수 있는 프로토타입까지</h1>
      <p>오늘 우리가 하는 것은 <b>AI-PLC</b>(AI-Driven Product Life Cycle)의 <b>Discovery phase</b>를 한 바퀴 도는 일입니다 — AWS가 공개한 제품 라이프사이클 방법론입니다(<a href="https://github.com/aws-samples/sample-ai-plc" style="color:var(--violet-deep)">aws-samples/sample-ai-plc</a>). 고객의 페인 포인트를 모으고, PR/FAQ로 구조화하고, <code>PROTOTYPE-*.md</code> 명세를 만들고 — 그 명세를 <b>실제로 도는 앱</b>으로 빌드해 사용자에게 검증까지 받습니다. 그 절차를 브라우저에서 돌게 해 주는 도구가 <b>Pathfinder</b>입니다.</p>
```

- [ ] **Step 3: §1 제목과 Discovery 정의 문단**

`312`행 제목:
```html
      <h2>왜 Discovery인가 <span class="badge">인트로</span></h2>
```
바꿀 문자열:
```html
      <h2>왜 AI-PLC Discovery인가 <span class="badge">인트로</span></h2>
```

`315`행:
```html
      <p>Discovery는 코드 이전에 답을 정하는 단계입니다 — <b>누구의</b>, <b>어떤 문제</b>를, <b>왜 지금</b> 풀어야 하는가. 그리고 그 답이 맞았는지 <b>사용자에게 확인</b>하는 단계입니다.</p>
```
바꿀 문자열:
```html
      <p>AI-PLC는 이 질문에 답하는 순서를 규정한 방법론입니다. 그 첫 phase인 <b>Discovery</b>가 코드 이전에 답을 정합니다 — <b>누구의</b>, <b>어떤 문제</b>를, <b>왜 지금</b> 풀어야 하는가. 그리고 그 답이 맞았는지 <b>사용자에게 확인</b>합니다.</p>
```

- [ ] **Step 4: §1 카드 3번 — Inception 언급에 근거 부여**

`327-329`행. 현재 근거 없이 "Inception"을 쓰고 있다. 다음 Step에서 삽입할 라이프사이클 그림이 그 근거가 되므로, 카드 문구도 phase 이름을 명확히 한다.

찾을 문자열:
```html
        <div class="card">
          <h4><span class="ico">🤝</span>개발로 넘길 수 있게 남긴다</h4>
          <p>결과는 대화가 아니라 문서입니다. 개발팀이 그대로 받아 Inception을 시작할 수 있는 형태로 남습니다.</p>
        </div>
```
바꿀 문자열:
```html
        <div class="card">
          <h4><span class="ico">🤝</span>개발로 넘길 수 있게 남긴다</h4>
          <p>결과는 대화가 아니라 <b>Discovery Document</b>입니다. 개발팀이 그대로 받아 다음 phase인 <b>Inception</b>을 시작할 수 있는 형태입니다.</p>
        </div>
```

- [ ] **Step 5: 라이프사이클 그림 삽입**

`332-335`행의 `💡 오늘 여러분이 직접 느낼 것` 노트 **앞에** 삽입한다. 기존 `.cards.c4` + `.card` 클래스만 쓴다(`131`행에 `c4` 정의가 있고 `259`행에 반응형 규칙도 있다). 새 CSS를 추가하지 않는다.

찾을 문자열:
```html
      <div class="note info">
        <div class="h">💡 오늘 여러분이 직접 느낄 것</div>
```
바꿀 문자열 — 위 문자열 앞에 다음을 붙인다:
```html
      <h3>AI-PLC — AI-Driven Product Life Cycle</h3>
      <p class="lead">방법론은 네 개의 phase로 이루어집니다. <b>오늘 우리는 첫 번째 phase만</b> 돕니다.</p>

      <div class="cards c4">
        <div class="card" style="border-color:rgba(109,94,252,.45); box-shadow:var(--glow)">
          <h4><span class="ico">🟣</span>DISCOVERY</h4>
          <p><b>◀ 오늘 여기</b><br>PM 주도 · 문제 정의<br><span style="font-family:var(--mono); font-size:12px">WHO · WHAT</span></p>
        </div>
        <div class="card">
          <h4><span class="ico">🔵</span>INCEPTION</h4>
          <p>요구사항 · 워크플로<br><span style="font-family:var(--mono); font-size:12px">WHAT · WHY</span></p>
        </div>
        <div class="card">
          <h4><span class="ico">🟢</span>CONSTRUCTION</h4>
          <p>설계 · 코드 · 테스트<br><span style="font-family:var(--mono); font-size:12px">HOW</span></p>
        </div>
        <div class="card">
          <h4><span class="ico">🟡</span>OPERATIONS</h4>
          <p>배포 · 운영<br><span style="font-family:var(--mono); font-size:12px">DEPLOY · RUN</span></p>
        </div>
      </div>
      <!-- Text fallback: AI-PLC has four phases — Discovery (PM-led problem definition, today), Inception (requirements, workflow), Construction (design, code, test), Operations (deploy, run). Today's output, the Discovery Document, is Inception's input. -->

      <div class="note info">
        <div class="h">🧭 오늘의 산출물이 어디로 가는가</div>
        <p>오늘의 산출물(<b>Discovery Document</b>)이 <b>Inception의 입력</b>이 됩니다 — 그래서 오늘 만드는 프로토타입은 납품물이 아니라 <b>다음 단계로 넘기는 근거</b>입니다. 개발 조직은 이 문서를 받아 별도 워크스페이스에서 Inception부터 시작합니다.</p>
      </div>

      <div class="note info">
        <div class="h">💡 오늘 여러분이 직접 느낄 것</div>
```

**주의**: 위 교체는 기존 `<div class="note info">` + `<div class="h">💡 ...` 두 줄을 그대로 끝에 포함하므로 원래 노트가 사라지지 않는다. 원본에서 이 두 줄만 매칭되도록 유일성을 확인할 것 — `💡 오늘 여러분이 직접 느낄 것`은 파일에 한 번만 나온다.

- [ ] **Step 6: §2 제목과 리드 — 상류 용어로**

`340-341`행:
```html
      <h2>Discovery — 세 개의 출발점</h2>
      <p class="lead">제품 Discovery는 <b>상황에 따라 다른 지점에서 시작</b>합니다. 워크플로우가 일을 따라가지, 일이 워크플로우를 따라가지 않습니다.</p>
```
바꿀 문자열:
```html
      <h2>AI-PLC Discovery — 세 개의 진입점</h2>
      <p class="lead">방법론은 <b>상황에 따라 다른 지점에서 시작</b>할 수 있게 세 개의 <b>Entry Point</b>를 규정합니다. 워크플로우가 일을 따라가지, 일이 워크플로우를 따라가지 않습니다.</p>
```

- [ ] **Step 7: 검증 — 첫 등장 규칙과 약어 확장**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① 약어 확장이 Product인지, Development가 없는지
grep -c "AI-Driven Product Life Cycle" docs/workshop/intro.html
grep -c "AI-Driven Development Life Cycle" docs/workshop/intro.html || echo "OK: Development 표기 없음"
# ② 리포 링크 존재
grep -c "github.com/aws-samples/sample-ai-plc" docs/workshop/intro.html
# ③ 첫 AI-PLC 등장 위치가 hero 리드(300행 근처)인지
grep -n "AI-PLC" docs/workshop/intro.html | head -3
```
Expected: ①은 `1` 이상 + "OK: Development 표기 없음" ②는 `1` 이상 ③ 첫 히트가 `#why` 섹션(312행) 이전, 즉 hero 리드(301행 근처)여야 한다. 네비 메뉴(283행)의 `왜 AI-PLC인가`가 먼저 나오는 것은 허용된다 — 네비는 목차이고 풀네임은 본문 첫 등장에 있으면 된다.

- [ ] **Step 8: 브라우저 렌더 확인**

Run: `python3 -m http.server 8899 --directory docs/workshop &` 후 브라우저에서 `http://localhost:8899/intro.html` 열기. 또는 파일을 직접 열기.
확인: ① 라이프사이클 4칸 카드가 한 줄로 표시되고 Discovery 칸에 보라 테두리+글로우가 있는지 ② 창을 좁혔을 때 2칸→1칸으로 접히는지(`259`·`266`행 반응형) ③ hero의 리포 링크가 보라색으로 보이고 클릭되는지.
서버를 띄웠으면 확인 후 종료: `kill %1`

- [ ] **Step 9: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/intro.html
git commit -m "docs(workshop): intro §1·§2를 AI-PLC 실명으로, 4-phase 라이프사이클 삽입

Discovery가 4-phase 중 첫 phase임을 그림으로 보여준다. 근거 없이
쓰이던 'Inception' 언급에 좌표가 생기고, 프로토타입이 납품물이 아닌
이유가 방법론상 당연한 것으로 바뀐다."
```

---

### Task 2: intro.html §3 — Pathfinder 정체성 재작성

스펙의 가장 중요한 작업이다. `#pathfinder` 섹션을 "방법론을 웹으로 옮긴 도구"에서 **"AI-PLC의 원래 실행 환경은 개발자 도구다. Pathfinder는 비개발자가 같은 절차를 돌 수 있게 한다"**로 바꾸고 대조표를 삽입한다.

**Files:**
- Modify: `docs/workshop/intro.html:285` (네비 메뉴 3번)
- Modify: `docs/workshop/intro.html:419-421` (§3 제목·리드·본문)
- Modify: `docs/workshop/intro.html:425-427` (카드 1번)
- Modify: `docs/workshop/intro.html:422` 근처 (대조표 신규 삽입)

**Interfaces:**
- Consumes: Task 1이 확정한 "AI-PLC = 방법론 / Pathfinder = 실행체" 프레이밍.
- Produces: 대조표 5행의 문안. Task 5(`pitch-internal.html` FAQ Q1)와 Task 6(`facilitator.html` FAQ)이 같은 논지의 축약본을 쓴다 — `[Answer]:` 태그 근거는 이 태스크에서 확정한 표현을 따른다.

- [ ] **Step 1: 네비 메뉴 3번**

`285`행:
```html
        <a href="#pathfinder"><span class="n">3</span>Pathfinder란</a>
```
바꿀 문자열:
```html
        <a href="#pathfinder"><span class="n">3</span>왜 Pathfinder인가</a>
```

- [ ] **Step 2: §3 제목·리드·본문 재작성**

`419-421`행을 교체한다. 핵심은 **왼쪽(원래 실행 환경)을 "번거롭다"가 아니라 "비개발자에게는 불가능하다"로 읽히게** 쓰는 것이다.

찾을 문자열:
```html
      <h2>Pathfinder란 무엇인가</h2>
      <p class="lead">제품 Discovery 방법론을 <b>웹 캔버스</b>로 옮긴 도구입니다. 터미널도, 설치도 필요 없습니다 — 브라우저만 있으면 됩니다.</p>
      <p>Pathfinder 안에서는 <b>Discovery 에이전트</b>가 방법론 문서를 직접 읽고 그 절차대로 움직입니다. 여러분은 채팅으로 답하고, 질문 폼을 채우고, 게이트에서 승인합니다. 에이전트가 만든 문서는 실시간으로 화면에 나타나고, 모든 결정은 감사 기록으로 남습니다.</p>
```
바꿀 문자열:
```html
      <h2>왜 Pathfinder인가</h2>
      <p class="lead">AI-PLC에는 <b>모순이 하나</b> 있습니다. 대상은 PM인데, 원래 실행 환경은 개발자 도구입니다.</p>
      <p>방법론은 Claude Code나 Kiro 같은 AI 코딩 도구 안에서 도는 것을 전제로 쓰였습니다. 룰이 요구하는 것을 그대로 보면 이렇습니다 — 워크스페이스를 Git으로 관리하고, 에이전트가 만든 <code>*-questions.md</code> 파일을 <b>직접 열어</b> <code>[Answer]:</code> 태그 뒤에 답을 타이핑하고, 생성된 문서를 파일 탐색기로 찾아 읽습니다. 상류 룰은 <b>"질문을 채팅으로 하지 말고 반드시 파일에 넣으라"</b>고 규정합니다.</p>
      <p><b>PM·기획자에게 이것은 번거로운 일이 아니라 불가능한 일입니다.</b> 그래서 방법론은 공개돼 있어도 정작 대상 독자가 혼자 돌릴 수 없었습니다. Pathfinder는 <b>그 간극만</b> 메웁니다 — 절차는 방법론 그대로 두고, 사용자가 만나는 부분을 브라우저로 바꿉니다.</p>

      <table>
        <thead><tr><th>AI-PLC 룰이 요구하는 것</th><th>Pathfinder에서는</th></tr></thead>
        <tbody>
          <tr>
            <td>Claude Code · Kiro 등 AI 코딩 도구 설치·설정</td>
            <td><b>브라우저만</b></td>
          </tr>
          <tr>
            <td>워크스페이스를 Git으로 만들고 관리</td>
            <td>프로젝트가 <b>자동 생성·보관</b></td>
          </tr>
          <tr>
            <td><code>*-questions.md</code> 파일을 열어 <code>[Answer]:</code> 태그 뒤에 답 입력</td>
            <td><b>질문 위저드 폼</b>에서 선택·입력</td>
          </tr>
          <tr>
            <td>생성된 문서를 파일 탐색기·에디터로 찾아 읽기</td>
            <td>화면 옆 <b>살아있는 문서 패널</b></td>
          </tr>
          <tr>
            <td>프로토타입 빌드·호스팅 환경을 직접 구성</td>
            <td>카드의 <b>버튼 → 링크로 열림</b></td>
          </tr>
        </tbody>
      </table>

      <p>Pathfinder 안에서는 <b>Discovery 에이전트</b>가 그 방법론 문서를 직접 읽고 절차대로 움직입니다. 여러분은 채팅으로 답하고, 질문 폼을 채우고, 게이트에서 승인합니다. 에이전트가 만든 문서는 실시간으로 화면에 나타나고, 모든 결정은 감사 기록으로 남습니다.</p>
```

- [ ] **Step 3: 카드 1번 — "내장"의 의미를 방법론으로**

`424-427`행:
```html
        <div class="card">
          <h4><span class="ico">🧭</span>방법론이 내장되어 있다</h4>
          <p>"다음에 무엇을 할지"를 여러분이 기억할 필요가 없습니다. 에이전트가 정해진 절차를 따라 다음 단계를 이끕니다.</p>
        </div>
```
바꿀 문자열:
```html
        <div class="card">
          <h4><span class="ico">🧭</span>AI-PLC 룰이 그대로 들어있다</h4>
          <p>에이전트가 방법론 룰 파일을 읽고 그 절차대로 진행합니다. <b>다르게 만든 워크플로가 아닙니다</b> — 진행 언어만 한국어로 지정했습니다.</p>
        </div>
```

- [ ] **Step 4: 검증 — 금지 표현과 근거 표현**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① 금지: "그대로 싣는다"류의 무수정 주장이 없는지
grep -n "무수정\|손대지 않\|수정 없이" docs/workshop/intro.html || echo "OK: 무수정 주장 없음"
# ② 대조표의 핵심 근거가 들어갔는지
grep -c "\[Answer\]:" docs/workshop/intro.html
# ③ 새 CSS 규칙을 추가하지 않았는지 — style 블록 라인 수가 그대로인지
awk '/^<style>/,/^<\/style>/' docs/workshop/intro.html | wc -l
```
Expected: ① "OK: 무수정 주장 없음" ② `1` 이상 ③ Task 1 이전 값과 동일해야 한다. 값을 미리 기록해 두지 않았으면 `git stash` 후 비교하거나 `git diff --stat`으로 `<style>` 범위 변경이 없음을 확인한다:
```bash
git diff docs/workshop/intro.html | grep "^[+-]" | grep -c "^\+.*{.*}" || echo "OK: CSS 규칙 추가 없음"
```

- [ ] **Step 5: 브라우저 렌더 확인**

`intro.html`을 열고 §3으로 스크롤. 확인: ① 대조표가 기존 표(§5 Path A 표)와 같은 모양인지 ② `<code>` 태그가 모노스페이스로 보이는지 ③ 표가 좁은 창에서 깨지지 않는지.

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/intro.html
git commit -m "docs(workshop): intro §3 재작성 — Pathfinder의 존재 이유를 방법론의 모순으로

AI-PLC는 대상이 PM인데 실행 환경이 개발자 도구다. 상류 룰이 질문을
*-questions.md 파일의 [Answer]: 태그로 받도록 규정하므로, PM에게는
번거로움이 아니라 불가능이다. 대조표로 그 간극만 메운다는 것을 보인다."
```

---

### Task 3: intro.html — stage 용어 정합화

`#tour` 이후 섹션들의 "단계" 표기를 상류 stage 이름으로 맞추고, Working Backwards·`PROTOTYPE-*.md`·환경 정리 안내에 방법론 근거를 붙인다.

**Files:**
- Modify: `docs/workshop/intro.html:522-526` (§5 제목·리드·표 헤더)
- Modify: `docs/workshop/intro.html:539` (PR/FAQ 행)
- Modify: `docs/workshop/intro.html:544` (솔루션 분석 행)
- Modify: `docs/workshop/intro.html:549` (프로토타입 명세 행)
- Modify: `docs/workshop/intro.html:557-558` (Working Backwards 노트)
- Modify: `docs/workshop/intro.html:562-563` (전달 가능 산출물 노트)
- Modify: `docs/workshop/intro.html:644` (§7 제목)
- Modify: `docs/workshop/intro.html:769` (환경 정리 경고)
- Modify: `docs/workshop/intro.html:779` (푸터)

**Interfaces:**
- Consumes: Task 1의 Phase/Stage 구분 규칙과 Entry Point 용어.

- [ ] **Step 1: §5 제목·리드와 표 헤더**

`522-523`행:
```html
      <h2>오늘 걷는 길 — Path A <span class="badge">페인 포인트 → 명세</span></h2>
      <p class="lead">고객의 불편에서 시작해 프로토타입 명세까지. 각 단계가 <b>다음 단계의 입력</b>이 되므로, 앞에서 잘 답할수록 뒤가 정확해집니다.</p>
```
바꿀 문자열:
```html
      <h2>오늘 걷는 길 — Path A <span class="badge">Entry Point 2 · 페인 포인트에서 시작</span></h2>
      <p class="lead">상류 룰이 <b>Entry Point 2</b>로 규정한 경로입니다. 고객의 불편에서 시작해 <code>PROTOTYPE-*.md</code> 명세까지. 각 stage가 <b>다음 stage의 입력</b>이 되므로, 앞에서 잘 답할수록 뒤가 정확해집니다.</p>
```

`526`행 표 헤더:
```html
        <thead><tr><th>단계</th><th>무엇을 하나</th><th>여러분이 결정할 것</th></tr></thead>
```
바꿀 문자열:
```html
        <thead><tr><th>Stage</th><th>무엇을 하나</th><th>여러분이 결정할 것</th></tr></thead>
```

- [ ] **Step 2: 표의 stage 이름에 영문 병기**

`539`행:
```html
            <td><span class="s">PR/FAQ</span><br><span class="pill gate">게이트</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Envision</span><br><span style="font-size:12px; color:var(--muted)">PR/FAQ</span><br><span class="pill gate">게이트</span></td>
```

`544`행:
```html
            <td><span class="s">솔루션 분석</span><br><span class="pill gate">게이트</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Solution Analysis</span><br><span style="font-size:12px; color:var(--muted)">솔루션 분석</span><br><span class="pill gate">게이트</span></td>
```

`549`행:
```html
            <td><span class="s">프로토타입 명세</span><br><span class="pill gate">게이트</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Prototype Spec</span><br><span style="font-size:12px; color:var(--muted)"><code>PROTOTYPE-*.md</code></span><br><span class="pill gate">게이트</span></td>
```

- [ ] **Step 3: Working Backwards 노트에 AWS 표준 근거**

`557-558`행. 마지막 문장 뒤에 근거 한 문장을 덧붙인다.

찾을 문자열:
```html
        <p>PR/FAQ는 <b>제품이 이미 출시된 것처럼</b> 쓰는 보도자료입니다. "우리는 ~을 만들 계획이다"가 아니라 "오늘 ~가 출시되었다. 이제 고객은 ~할 수 있다"로 씁니다. 어색한 게 정상입니다 — 그 어색함이 <b>고객 관점에서 다시 생각하게</b> 만드는 장치입니다. 읽었을 때 "그래서 뭐가 좋아지는데?"에 답이 없으면, 아직 문제를 덜 이해한 것입니다.</p>
```
바꿀 문자열:
```html
        <p>PR/FAQ는 <b>제품이 이미 출시된 것처럼</b> 쓰는 보도자료입니다. "우리는 ~을 만들 계획이다"가 아니라 "오늘 ~가 출시되었다. 이제 고객은 ~할 수 있다"로 씁니다. 어색한 게 정상입니다 — 그 어색함이 <b>고객 관점에서 다시 생각하게</b> 만드는 장치입니다. 읽었을 때 "그래서 뭐가 좋아지는데?"에 답이 없으면, 아직 문제를 덜 이해한 것입니다.</p>
        <p style="margin-top:10px">이것은 이 워크샵의 규칙이 아니라 <b>Working Backwards</b> — 아마존이 신규 제품을 정의할 때 쓰는 방식이고, AI-PLC가 <b>Envision</b> stage의 산출물로 규정한 것입니다.</p>
```

- [ ] **Step 4: 전달 가능 산출물 노트 — Entry Point 1과 연결**

`562-563`행:
```html
        <div class="h">📌 명세는 "전달 가능한" 산출물입니다</div>
        <p>프로토타입 명세는 오늘 우리가 빌드하는 데도 쓰이지만, <b>그대로 다른 팀에 넘길 수도</b> 있습니다. 명세를 받은 팀은 Discovery를 처음부터 다시 하지 않고 바로 빌드부터 시작합니다(앞에서 본 "기존 명세" 경로). 워크샵이나 여러 팀이 나눠 만드는 상황에서 특히 유용합니다.</p>
```
바꿀 문자열:
```html
        <div class="h">📌 <code>PROTOTYPE-*.md</code>는 "전달 가능한" 산출물입니다</div>
        <p>이 명세 파일은 오늘 우리가 빌드하는 데도 쓰이지만, <b>파일 그대로 다른 팀에 넘길 수도</b> 있습니다. 받은 팀은 Discovery를 처음부터 다시 하지 않고 바로 빌드부터 시작합니다 — 앞에서 본 <b>Entry Point 1</b>이 정확히 이 경우입니다. 상류 룰이 <b>워크샵과 병렬 작업을 염두에 두고</b> 이 파일을 이식 가능한 형식으로 규정했습니다.</p>
```

- [ ] **Step 5: §7 제목 — Prototype & Validation stage 명시**

`644`행:
```html
      <h2>검증 루프 — Discovery가 닫히는 곳</h2>
```
바꿀 문자열:
```html
      <h2>검증 루프 — Discovery phase가 닫히는 곳 <span class="badge">Prototype &amp; Validation</span></h2>
```

- [ ] **Step 6: 환경 정리 경고 — 방법론이 공개돼 있다는 약속**

`769`행:
```html
        <p>워크샵이 끝나면 <b>실습 환경은 정리됩니다.</b> 환경이 사라지면 그 안의 산출물도 함께 없어지므로, <b>마치기 전에 zip을 꼭 받아 두세요</b> — 문서 리뷰 탭의 <b>전체 다운로드</b>와 프로토타입 카드의 <b>다운로드</b> 두 개입니다. 받아 두면 사내에서 다시 열어보고 이어서 쓸 수 있습니다.</p>
```
바꿀 문자열:
```html
        <p>워크샵이 끝나면 <b>실습 환경은 정리됩니다.</b> 환경이 사라지면 그 안의 산출물도 함께 없어지므로, <b>마치기 전에 zip을 꼭 받아 두세요</b> — 문서 리뷰 탭의 <b>전체 다운로드</b>와 프로토타입 카드의 <b>다운로드</b> 두 개입니다.</p>
        <p style="margin-top:10px">환경은 사라지지만 <b>방법론은 남습니다.</b> AI-PLC 룰은 <a href="https://github.com/aws-samples/sample-ai-plc" style="color:var(--violet-deep)">공개 리포지토리</a>에 있으므로, 오늘 받아 간 산출물을 들고 사내에서 같은 절차를 이어서 돌릴 수 있습니다.</p>
```

- [ ] **Step 7: 푸터**

`779`행:
```html
      Pathfinder: From Pain Point to Validated Prototype · 워크샵 소개자료 · Path A(페인 포인트) 경로 기준<br>
```
바꿀 문자열:
```html
      AWS AI-PLC Discovery 핸즈온 · 워크샵 소개자료 · Path A(Entry Point 2 · 페인 포인트) 경로 기준 · 구동 도구 Pathfinder<br>
      방법론 원본: <a href="https://github.com/aws-samples/sample-ai-plc" style="color:inherit">github.com/aws-samples/sample-ai-plc</a><br>
```

- [ ] **Step 8: 검증 — Phase/Stage 혼용 없는지**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① stage 이름에 "단계"나 "phase"가 잘못 붙었는지
grep -n "Envision 단계\|Envision phase\|Solution Analysis 단계\|Go-to-Market 단계\|Product Strategy 단계" docs/workshop/intro.html || echo "OK: stage에 단계/phase 오용 없음"
# ② phase 이름에 "stage"가 붙었는지
grep -n "Discovery stage\|Inception stage\|Construction stage" docs/workshop/intro.html || echo "OK: phase에 stage 오용 없음"
# ③ PROTOTYPE 파일명 노출 확인
grep -c "PROTOTYPE-\*\.md\|PROTOTYPE-{slug}\.md" docs/workshop/intro.html
```
Expected: ①② 두 "OK" 메시지 ③ `3` 이상

- [ ] **Step 9: 브라우저 렌더 확인 후 커밋**

`intro.html`을 열어 §5 표의 stage 셀이 3줄(영문/한글/게이트 배지)로 깨지지 않는지, §7 제목의 badge가 길어서 줄바꿈되지 않는지 확인한다.

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/intro.html
git commit -m "docs(workshop): intro의 stage 용어를 상류 규정에 맞춤

'단계'로 뭉쳐 쓰던 것을 Stage 영문명으로 병기하고, Path A가 Entry
Point 2임을 명시한다. Working Backwards와 PROTOTYPE-*.md의 근거를
방법론으로 돌린다. 환경은 사라지지만 방법론은 공개돼 있다는 약속 추가."
```

---

### Task 4: edm.html — 고객 초대 메일

독자는 아직 신청하지 않은 PM·기획자. AI-PLC를 **신뢰 근거**로만 쓰고 설명하지 않는다. **제목에는 약어를 넣지 않는다** — 미지의 약어가 제목에 있으면 열람률이 떨어진다.

**Files:**
- Modify: `docs/workshop/edm.html:34` (헤더 킥커)
- Modify: `docs/workshop/edm.html:43` (헤더 서브)
- Modify: `docs/workshop/edm.html:133-142` (🎯 박스)
- Modify: `docs/workshop/edm.html:144` 뒤 (라이프사이클 테이블 신규)
- Modify: `docs/workshop/edm.html:168,183,198,213` (4단계 흐름 각 제목에 stage 병기)
- Modify: `docs/workshop/edm.html:388` (푸터)

**Interfaces:**
- Consumes: Task 1이 확정한 라이프사이클 4칸 문안. 마크업만 테이블로 바꾼다.

**CRITICAL — 이메일 제약**: `<pre>`, `flex`, `gap`, `grid`, `<style>` 블록을 쓰지 않는다. 레이아웃은 `<table>`, 스타일은 인라인 `style=`만. 파일 `45`행 주석이 같은 제약을 기록한다: *"이메일에서 flex/gap이 안 먹으므로 셀 사이 간격은 padding으로 만든다"*.

- [ ] **Step 1: 헤더 킥커와 서브**

`34`행:
```html
                  Pathfinder: From Pain Point to Validated Prototype
```
바꿀 문자열:
```html
                  AWS AI-PLC Discovery · 반일 핸즈온
```

`43`행:
```html
                  Pathfinder 워크샵 · 반일 핸즈온
```
바꿀 문자열:
```html
AI-PLC Discovery 워크샵 · Pathfinder로 진행
```
(들여쓰기는 원본과 동일하게 18칸 유지)

제목(`36-41`행)은 **변경하지 않는다** — 고객 언어를 유지한다.

- [ ] **Step 2: 🎯 박스 — 신뢰 근거를 심는다**

`133-142`행. 이 파일의 AI-PLC 첫 등장이므로 **풀네임과 리포 URL이 여기 들어간다.**

찾을 문자열:
```html
                    <div style="font-size: 14.5px; color: rgb(28, 30, 54); font-weight: bold; margin-bottom: 6px;">
                      🎯 Pathfinder는 이 앞단을 반나절로 줄입니다
                    </div>
                    <div style="font-size: 13.5px; color: rgb(46, 48, 78); line-height: 1.74;">
                      문제 정리 → 명세 → 만져볼 수 있는 프로토타입 → 사람들의 반응까지, <b>기다리지 않고 한 번에</b> 갑니다. 개발 일정을 잡기 전에 <b>이 방향이 맞는지 먼저 알 수 있게</b> 하는 것이 목적입니다.
                      <br><br>
                      그래서 여기서 만드는 프로토타입은 <b>납품물이 아니라 판단의 근거</b>입니다. 운영·인증·연동 같은 실제 구축은 개발 조직의 몫으로 남겨 둡니다 &mdash; 대신 그쪽에 넘길 때 <b>검증까지 끝난 기획</b>을 함께 넘기게 됩니다.
                      <br><br>
                      한 번 해 보면 이 흐름은 <b>내 업무의 다른 과제에도 그대로 다시 쓸 수 있습니다.</b> 워크샵이 끝나고 실제로 남는 건 그 방법입니다.
                    </div>
```
바꿀 문자열:
```html
                    <div style="font-size: 14.5px; color: rgb(28, 30, 54); font-weight: bold; margin-bottom: 6px;">
                      🎯 임의로 만든 커리큘럼이 아닙니다
                    </div>
                    <div style="font-size: 13.5px; color: rgb(46, 48, 78); line-height: 1.74;">
                      이 워크샵은 AWS가 공개한 방법론 <b>AI-PLC</b>(AI-Driven Product Life Cycle)의 <b>Discovery</b> 구간을 그대로 따라갑니다. 문제 정리 → 명세 → 만져볼 수 있는 프로토타입 → 사람들의 반응까지, <b>기다리지 않고 한 번에</b> 갑니다. 개발 일정을 잡기 전에 <b>이 방향이 맞는지 먼저 알 수 있게</b> 하는 것이 방법론의 목적입니다.
                      <br><br>
                      그래서 여기서 만드는 프로토타입은 <b>납품물이 아니라 판단의 근거</b>입니다. 운영·인증·연동 같은 실제 구축은 개발 조직의 몫으로 남겨 둡니다 &mdash; 대신 그쪽에 넘길 때 <b>검증까지 끝난 기획</b>을 함께 넘기게 됩니다.
                      <br><br>
                      방법론은 <b>공개되어 있습니다</b>(<span style="font-family: monospace; font-size: 12.5px;">github.com/aws-samples/sample-ai-plc</span>). 그래서 워크샵이 끝난 뒤에도 <b>사내에서 같은 절차를 이어서 쓸 수 있습니다.</b> 실제로 남는 건 그 방법입니다.
                    </div>
```

- [ ] **Step 3: 라이프사이클 테이블 신규 삽입**

`144-146`행의 🎯 박스 닫는 태그 뒤, `<!-- ===================== 4시간의 흐름 =====================` 주석 앞에 새 `<tr>`을 삽입한다. **4칸을 한 줄 테이블로** 만들고 Discovery 칸만 강조한다.

찾을 문자열:
```html
            <!-- ===================== 4시간의 흐름 ===================== -->
```
바꿀 문자열 — 위 주석 앞에 다음을 붙인다:
```html
            <!-- ===================== AI-PLC 라이프사이클 ===================== -->
            <tr>
              <td style="padding: 26px 34px 0;">
                <div style="font-size: 13px; color: rgb(84, 88, 116); margin-bottom: 10px;">
                  AI-PLC는 네 구간으로 이루어집니다. <b style="color: rgb(28, 30, 54);">이 워크샵은 첫 구간입니다.</b>
                </div>
                <table cellspacing="0" cellpadding="0" border="0" style="width: 100%; border: 1px solid rgb(223, 225, 245);">
                  <tbody>
                    <tr>
                      <td width="25%" bgcolor="rgb(240, 241, 253)" style="background-color: rgb(240, 241, 253); border-right: 1px solid rgb(223, 225, 245); padding: 12px 10px; text-align: center;">
                        <div style="font-size: 12px; font-weight: bold; color: rgb(76, 62, 210);">DISCOVERY</div>
                        <div style="font-size: 11px; color: rgb(76, 62, 210); margin-top: 3px;"><b>◀ 이 워크샵</b></div>
                        <div style="font-size: 11.5px; color: rgb(84, 88, 116); margin-top: 5px;">문제 정의</div>
                      </td>
                      <td width="25%" style="border-right: 1px solid rgb(223, 225, 245); padding: 12px 10px; text-align: center;">
                        <div style="font-size: 12px; font-weight: bold; color: rgb(120, 124, 152);">INCEPTION</div>
                        <div style="font-size: 11.5px; color: rgb(140, 144, 172); margin-top: 5px;">요구사항 · 워크플로</div>
                      </td>
                      <td width="25%" style="border-right: 1px solid rgb(223, 225, 245); padding: 12px 10px; text-align: center;">
                        <div style="font-size: 12px; font-weight: bold; color: rgb(120, 124, 152);">CONSTRUCTION</div>
                        <div style="font-size: 11.5px; color: rgb(140, 144, 172); margin-top: 5px;">설계 · 코드 · 테스트</div>
                      </td>
                      <td width="25%" style="padding: 12px 10px; text-align: center;">
                        <div style="font-size: 12px; font-weight: bold; color: rgb(120, 124, 152);">OPERATIONS</div>
                        <div style="font-size: 11.5px; color: rgb(140, 144, 172); margin-top: 5px;">배포 · 운영</div>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div style="font-size: 12.5px; color: rgb(120, 124, 152); line-height: 1.7; margin-top: 10px;">
                  오늘의 산출물이 다음 구간의 입력이 됩니다. 그래서 <b>기획을 확정하기 전에</b> 방향을 먼저 확인하는 자리입니다.
                </div>
              </td>
            </tr>

            <!-- ===================== 4시간의 흐름 ===================== -->
```

- [ ] **Step 4: 4단계 흐름에 stage 이름 병기**

`168`행부터 시작하는 4단계 파이프라인의 각 제목에 stage 이름을 붙인다. 첫 항목:

찾을 문자열:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">문제를 정리한다</div>
```
바꿀 문자열:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">문제를 정리한다 <span style="font-size: 11.5px; color: rgb(140, 144, 172); font-weight: normal;">Envision</span></div>
```

나머지 3개(`183`, `198`, `213`행)도 같은 패턴으로 처리한다.

`183`행:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">아이디어를 명세로 만든다</div>
```
바꿀 문자열:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">아이디어를 명세로 만든다 <span style="font-size: 11.5px; color: rgb(140, 144, 172); font-weight: normal;">Solution Analysis</span></div>
```

`198`행:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">프로토타입으로 만든다</div>
```
바꿀 문자열:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">프로토타입으로 만든다 <span style="font-size: 11.5px; color: rgb(140, 144, 172); font-weight: normal;">Prototype Building</span></div>
```

`213`행:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">쓸 만한지 물어본다</div>
```
바꿀 문자열:
```html
                        <div style="font-size: 15px; color: rgb(28, 30, 54); font-weight: bold;">쓸 만한지 물어본다 <span style="font-size: 11.5px; color: rgb(140, 144, 172); font-weight: normal;">Prototype &amp; Validation</span></div>
```

**`&`는 반드시 `&amp;`로 이스케이프한다.**

- [ ] **Step 5: 푸터**

`388`행:
```html
                  Pathfinder: From Pain Point to Validated Prototype · 반일 핸즈온<br>
```
바꿀 문자열:
```html
                  AWS AI-PLC Discovery 핸즈온 · 반일 · 구동 도구 Pathfinder<br>
                  방법론 원본: <span style="font-family: monospace;">github.com/aws-samples/sample-ai-plc</span><br>
```

- [ ] **Step 6: 검증 — 이메일 제약과 약어 규칙**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① 이메일 금지 요소가 없는지
grep -n "<pre\|display: *flex\|display:flex\|gap:\|display: *grid\|display:grid\|<style" docs/workshop/edm.html || echo "OK: 이메일 금지 요소 없음"
# ② 약어 확장
grep -c "AI-Driven Product Life Cycle" docs/workshop/edm.html
grep -n "AI-Driven Development Life Cycle" docs/workshop/edm.html || echo "OK: Development 표기 없음"
# ③ 제목에 약어가 없는지 — 36~41행에 AI-PLC가 없어야 한다
sed -n '36,41p' docs/workshop/edm.html | grep "AI-PLC" && echo "FAIL: 제목에 약어가 들어갔다" || echo "OK: 제목은 고객 언어"
# ④ & 이스케이프 확인 — 벌거벗은 &가 없는지
grep -n "&[^a-z#]" docs/workshop/edm.html || echo "OK: & 이스케이프 정상"
# ⑤ 리포 URL
grep -c "aws-samples/sample-ai-plc" docs/workshop/edm.html
```
Expected: ① "OK: 이메일 금지 요소 없음" ② `1` + "OK: Development 표기 없음" ③ "OK: 제목은 고객 언어" ④ "OK: & 이스케이프 정상" ⑤ `2` 이상

- [ ] **Step 7: 렌더 확인**

`edm.html`을 브라우저에서 열고 확인: ① 라이프사이클 4칸이 한 줄로 균등 분할되는지 ② Discovery 칸만 배경이 연보라인지 ③ 720px 폭에서 4칸 텍스트가 넘치지 않는지 ④ 4단계 흐름의 stage 병기가 제목과 같은 줄에 회색으로 붙는지.

추가로 좁은 화면(모바일 클라이언트 시뮬레이션)에서 4칸 테이블이 심하게 찌그러지지 않는지 확인한다. 텍스트가 세로로 쌓여 읽히면 허용 — 이메일에서는 미디어 쿼리를 신뢰할 수 없으므로 `width="25%"`로 균등 분할만 보장한다.

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/edm.html
git commit -m "docs(workshop): EDM에 AI-PLC를 신뢰 근거로 심고 라이프사이클 노출

제목은 고객 언어를 유지하고(미지의 약어는 열람률을 떨어뜨린다) 본문
포지셔닝 박스에서 AWS 공개 방법론임을 밝힌다. '다시 쓸 수 있다'는
막연한 약속을 공개 리포라는 근거로 바꿨다. 이메일 제약(table+인라인
CSS, flex/gap 금지)을 지켰다."
```

---

### Task 5: pitch-internal.html — SA/AM 내부 공유

AI-PLC가 **재사용 가능한 AWS 자산**이라는 점이 세일즈 논리가 된다. 고객 FAQ 3문답을 신규 추가한다.

**Files:**
- Modify: `docs/workshop/pitch-internal.html:6` (title)
- Modify: `docs/workshop/pitch-internal.html:25-31` (헤더 킥커·제목·서브)
- Modify: `docs/workshop/pitch-internal.html:77-81` (얻는 것 — 3번째 항목 신규)
- Modify: `docs/workshop/pitch-internal.html:143-161` (⚖️ 포지셔닝 박스)
- Modify: `docs/workshop/pitch-internal.html:241-243` (배제 조건)
- Modify: `docs/workshop/pitch-internal.html:247` 뒤 (FAQ 블록 신규)
- Modify: `docs/workshop/pitch-internal.html:281` (푸터)

**Interfaces:**
- Consumes: Task 1의 라이프사이클 문안, Task 2의 대조표 논지(`[Answer]:` 근거).
- Produces: FAQ 3문답의 정확한 문안. Task 6이 `facilitator.html`에 같은 3문답을 넣는다.

**제약**: 이 파일도 이메일용이다(SA/AM에게 메일로 보낸다). Task 4와 같은 이메일 제약을 지킨다 — `<table>` + 인라인 CSS만.

- [ ] **Step 1: title과 헤더**

`6`행:
```html
<title>Pathfinder 워크샵 — 고객 딜리버리 제안 (SA/AM 내부 공유용)</title>
```
바꿀 문자열:
```html
<title>AI-PLC Discovery 워크샵 — 고객 딜리버리 제안 (SA/AM 내부 공유용)</title>
```

`27-31`행:
```html
      <div style="font-size:24px; line-height:1.42; color:rgb(255,255,255); font-weight:bold;">
        고객의 &ldquo;AI로 뭘 해야 할지 모르겠다&rdquo;에<br>반나절로 답을 주는 핸즈온
      </div>
      <div style="font-size:14.5px; line-height:1.7; color:rgb(196,201,230); margin-top:12px;">
        Pathfinder 워크샵 &mdash; 고객 PM·기획 조직이 <b style="color:rgb(255,255,255);">자기 업무 아이디어를 직접 프로토타입까지</b> 만들어 보는 자리입니다. 우리가 딜리버리할 수 있고, 준비는 대부분 자동화돼 있습니다.
      </div>
```
바꿀 문자열:
```html
      <div style="font-size:24px; line-height:1.42; color:rgb(255,255,255); font-weight:bold;">
        고객의 &ldquo;AI로 뭘 해야 할지 모르겠다&rdquo;에<br>반나절로 답을 주는 핸즈온
      </div>
      <div style="font-size:14.5px; line-height:1.7; color:rgb(196,201,230); margin-top:12px;">
        <b style="color:rgb(255,255,255);">AWS 공개 방법론 AI-PLC</b>(AI-Driven Product Life Cycle)의 Discovery 구간을 고객 PM·기획 조직과 반나절에 완주합니다. 구동 도구는 Pathfinder이고, 우리가 딜리버리할 수 있으며 준비는 대부분 자동화돼 있습니다.
      </div>
```

- [ ] **Step 2: "얻는 것"에 3번째 항목 신규**

`77-82`행의 두 번째 `<tr>` 뒤에 세 번째를 추가한다.

찾을 문자열:
```html
          <tr>
            <td valign="top" style="font-size:14px; color:rgb(52,55,82); line-height:1.74; padding-bottom:10px;">
              <b style="color:rgb(28,30,54);">유즈케이스가 고객의 언어로 나옵니다</b> &mdash; 우리가 제안한 목록이 아니라 <b>고객이 직접 고르고 검증한</b> 과제입니다. 다음 단계(PoC·MVP) 논의에서 근거가 이미 있는 상태로 시작합니다.
            </td>
          </tr>
        </tbody>
```
바꿀 문자열:
```html
          <tr>
            <td valign="top" style="font-size:14px; color:rgb(52,55,82); line-height:1.74; padding-bottom:10px;">
              <b style="color:rgb(28,30,54);">유즈케이스가 고객의 언어로 나옵니다</b> &mdash; 우리가 제안한 목록이 아니라 <b>고객이 직접 고르고 검증한</b> 과제입니다. 다음 단계(PoC·MVP) 논의에서 근거가 이미 있는 상태로 시작합니다.
            </td>
          </tr>
          <tr>
            <td valign="top" style="font-size:14px; color:rgb(52,55,82); line-height:1.74; padding-bottom:10px;">
              <b style="color:rgb(28,30,54);">고객에게 남는 것이 도구 종속이 아닙니다</b> &mdash; AI-PLC는 <b>AWS가 공개한 방법론</b>이므로(<span style="font-family:monospace; font-size:12.5px;">aws-samples/sample-ai-plc</span>) 고객이 워크샵 후에도 자체적으로 돌릴 수 있습니다. 그리고 방법론의 다음 구간이 Inception&middot;Construction이므로, <b>후속 논의가 방법론 안에서 자연스럽게 이어집니다.</b>
            </td>
          </tr>
        </tbody>
```

- [ ] **Step 3: 라이프사이클 테이블 삽입**

`87`행의 `<!-- ===================== 반나절 흐름 =====================` 주석 앞에 새 `<tr>`을 삽입한다. Task 4 Step 3과 같은 마크업이되, **후속 단계가 어디로 이어지는지**를 캡션에 넣는다.

찾을 문자열:
```html
  <!-- ===================== 반나절 흐름 ===================== -->
```
바꿀 문자열 — 위 주석 앞에 다음을 붙인다:
```html
  <!-- ===================== AI-PLC 라이프사이클 ===================== -->
  <tr>
    <td style="padding:28px 34px 0;">
      <table cellspacing="0" cellpadding="0" border="0" style="width:100%; border-left:4px solid rgb(109,94,252);">
        <tbody><tr><td bgcolor="rgb(244,245,255)" style="background-color:rgb(244,245,255); padding:8px 13px; font-size:15.5px; color:rgb(28,30,54);">
          <b>워크샵이 방법론 어디에 있는가</b>
        </td></tr></tbody>
      </table>

      <table cellspacing="0" cellpadding="0" border="0" style="width:100%; margin-top:14px; border:1px solid rgb(223,225,245);">
        <tbody>
          <tr>
            <td width="25%" bgcolor="rgb(240,241,253)" style="background-color:rgb(240,241,253); border-right:1px solid rgb(223,225,245); padding:12px 10px; text-align:center;">
              <div style="font-size:12px; font-weight:bold; color:rgb(76,62,210);">DISCOVERY</div>
              <div style="font-size:11px; color:rgb(76,62,210); margin-top:3px;"><b>◀ 이 워크샵</b></div>
              <div style="font-size:11.5px; color:rgb(84,88,116); margin-top:5px;">PM 주도 · 문제 정의</div>
            </td>
            <td width="25%" style="border-right:1px solid rgb(223,225,245); padding:12px 10px; text-align:center;">
              <div style="font-size:12px; font-weight:bold; color:rgb(120,124,152);">INCEPTION</div>
              <div style="font-size:11.5px; color:rgb(140,144,172); margin-top:5px;">요구사항 · 워크플로</div>
            </td>
            <td width="25%" style="border-right:1px solid rgb(223,225,245); padding:12px 10px; text-align:center;">
              <div style="font-size:12px; font-weight:bold; color:rgb(120,124,152);">CONSTRUCTION</div>
              <div style="font-size:11.5px; color:rgb(140,144,172); margin-top:5px;">설계 · 코드 · 테스트</div>
            </td>
            <td width="25%" style="padding:12px 10px; text-align:center;">
              <div style="font-size:12px; font-weight:bold; color:rgb(120,124,152);">OPERATIONS</div>
              <div style="font-size:11.5px; color:rgb(140,144,172); margin-top:5px;">배포 · 운영</div>
            </td>
          </tr>
        </tbody>
      </table>

      <p style="margin:12px 0 0; font-size:13.5px; color:rgb(52,55,82); line-height:1.7;">
        워크샵 산출물(<b>Discovery Document</b>)이 <b>Inception의 입력</b>입니다. 즉 워크샵이 끝난 지점이 <b>후속 제안의 출발점</b>이 됩니다 &mdash; &ldquo;다음은 무엇인가&rdquo;를 우리가 꺼내지 않아도 방법론이 이미 답을 갖고 있습니다.
      </p>
    </td>
  </tr>

  <!-- ===================== 반나절 흐름 ===================== -->
```

- [ ] **Step 4: ⚖️ 포지셔닝 박스 — 근거를 방법론으로 교체**

`148-157`행. 현재 "납품물이 아니다"를 자체 논리로 방어하고 있다. 방법론 근거로 바꾼다.

찾을 문자열:
```html
          <div style="font-size:14.5px; color:rgb(28,30,54); font-weight:bold; margin-bottom:7px;">
            ⚖️ 고객에게 이렇게 설명하세요 &mdash; 여기서 만든 것을 그대로 쓰는 자리가 아닙니다
          </div>
          <div style="font-size:13.5px; color:rgb(78,70,52); line-height:1.74;">
            프로토타입은 <b>납품물이 아니라 판단의 근거</b>입니다. 운영·인증·사내 연동 같은 실제 구축은 고객의 개발 조직(또는 후속 프로젝트)의 몫으로 남습니다.
            <br><br>
            이 자리의 목적은 <b>개발 일정을 잡기 전에 방향이 맞는지 먼저 확인하는 것</b>입니다. 그래서 산출물은 &ldquo;돌아가는 서비스&rdquo;가 아니라 <b>검증이 끝난 기획 문서 + 만져본 경험 + 반응 데이터</b>입니다.
            <br><br>
            이 선을 처음부터 그어야 &ldquo;이거 그대로 쓸 수 있나요?&rdquo; 하는 기대와 어긋나지 않습니다.
          </div>
```
바꿀 문자열:
```html
          <div style="font-size:14.5px; color:rgb(28,30,54); font-weight:bold; margin-bottom:7px;">
            ⚖️ 고객에게 이렇게 설명하세요 &mdash; 여기서 만든 것을 그대로 쓰는 자리가 아닙니다
          </div>
          <div style="font-size:13.5px; color:rgb(78,70,52); line-height:1.74;">
            <b>방법론을 근거로 설명하면 방어할 필요가 없습니다.</b> Discovery는 AI-PLC의 첫 구간이고, 그 산출물은 다음 구간(Inception)의 <b>입력</b>으로 규정돼 있습니다. 즉 프로토타입은 납품물이 아니라 <b>다음 단계로 넘기는 근거</b>입니다 &mdash; 우리가 범위를 좁혀서가 아니라 방법론이 그렇게 정의합니다.
            <br><br>
            운영·인증·사내 연동 같은 실제 구축은 Construction 구간이고, 고객의 개발 조직(또는 후속 프로젝트)의 몫입니다. 그래서 산출물은 &ldquo;돌아가는 서비스&rdquo;가 아니라 <b>검증이 끝난 기획 문서 + 만져본 경험 + 반응 데이터</b>입니다.
            <br><br>
            이 선을 처음부터 그어야 &ldquo;이거 그대로 쓸 수 있나요?&rdquo; 하는 기대와 어긋나지 않습니다.
          </div>
```

- [ ] **Step 5: 배제 조건에 Greenfield 근거**

`241-243`행:
```html
          <tr><td valign="top" style="font-size:13.5px; color:rgb(120,124,152); line-height:1.74; padding-top:4px;">
            ✖️&nbsp; 반대로, 이미 <b>만들 것이 확정</b>돼 구현·아키텍처 논의가 필요한 고객에게는 맞지 않습니다. 그 경우는 일반 Immersion Day가 낫습니다.
          </td></tr>
```
바꿀 문자열:
```html
          <tr><td valign="top" style="font-size:13.5px; color:rgb(120,124,152); line-height:1.74; padding-top:4px;">
            ✖️&nbsp; 반대로, 이미 <b>만들 것이 확정</b>돼 구현·아키텍처 논의가 필요한 고객에게는 맞지 않습니다. 이건 우리 판단이 아니라 방법론 규정입니다 &mdash; AI-PLC는 Discovery를 <b>신규 제품 정의(Greenfield) 전용</b>으로 규정하고, 기존 시스템 개선은 Inception 구간의 Reverse Engineering으로 보냅니다. 그 경우는 일반 Immersion Day가 낫습니다.
          </td></tr>
```

- [ ] **Step 6: FAQ 블록 신규 삽입**

`249`행의 `<!-- ===================== CTA =====================` 주석 앞에 삽입한다. Global Constraints 위쪽의 "고객 FAQ 3문답" 문안을 그대로 쓴다.

찾을 문자열:
```html
  <!-- ===================== CTA ===================== -->
```
바꿀 문자열 — 위 주석 앞에 다음을 붙인다:
```html
  <!-- ===================== 고객이 물을 것 ===================== -->
  <tr>
    <td style="padding:28px 34px 0;">
      <table cellspacing="0" cellpadding="0" border="0" style="width:100%; border-left:4px solid rgb(109,94,252);">
        <tbody><tr><td bgcolor="rgb(244,245,255)" style="background-color:rgb(244,245,255); padding:8px 13px; font-size:15.5px; color:rgb(28,30,54);">
          <b>고객이 물을 것 &mdash; 이렇게 답하세요</b>
        </td></tr></tbody>
      </table>

      <table cellspacing="0" cellpadding="0" border="0" style="width:100%; margin-top:14px; border:1px solid rgb(223,225,245);">
        <tbody>
          <tr>
            <td bgcolor="rgb(249,250,253)" style="background-color:rgb(249,250,253); padding:11px 14px; font-size:13.5px; color:rgb(28,30,54); border-bottom:1px solid rgb(223,225,245);">
              <b>&ldquo;리포를 받아서 직접 돌리면 되지 않나요?&rdquo;</b>
            </td>
          </tr>
          <tr>
            <td style="padding:11px 14px; font-size:13.5px; color:rgb(52,55,82); border-bottom:1px solid rgb(223,225,245); line-height:1.72;">
              돌릴 수 있습니다 &mdash; <b>Claude Code나 Kiro를 쓸 줄 아는 사람이라면.</b> 상류 룰은 질문을 채팅이 아니라 <span style="font-family:monospace; font-size:12.5px;">*-questions.md</span> 파일에 넣고 <span style="font-family:monospace; font-size:12.5px;">[Answer]:</span> 태그 뒤에 답하도록 규정합니다. 워크샵 참가자는 PM·기획자이고, 그들에게 필요한 것은 방법론이지 터미널이 아닙니다. Pathfinder는 <b>그 간극만</b> 메웁니다.
            </td>
          </tr>
          <tr>
            <td bgcolor="rgb(249,250,253)" style="background-color:rgb(249,250,253); padding:11px 14px; font-size:13.5px; color:rgb(28,30,54); border-bottom:1px solid rgb(223,225,245);">
              <b>&ldquo;AI-PLC가 뭔가요?&rdquo;</b>
            </td>
          </tr>
          <tr>
            <td style="padding:11px 14px; font-size:13.5px; color:rgb(52,55,82); border-bottom:1px solid rgb(223,225,245); line-height:1.72;">
              AWS가 공개한 AI 기반 제품 라이프사이클 방법론입니다(<span style="font-family:monospace; font-size:12.5px;">aws-samples/sample-ai-plc</span>). Discovery &rarr; Inception &rarr; Construction &rarr; Operations 네 구간이고, <b>워크샵은 첫 구간인 Discovery를 다룹니다.</b>
            </td>
          </tr>
          <tr>
            <td bgcolor="rgb(249,250,253)" style="background-color:rgb(249,250,253); padding:11px 14px; font-size:13.5px; color:rgb(28,30,54); border-bottom:1px solid rgb(223,225,245);">
              <b>&ldquo;Pathfinder는 AWS 제품인가요?&rdquo;</b>
            </td>
          </tr>
          <tr>
            <td style="padding:11px 14px; font-size:13.5px; color:rgb(52,55,82); line-height:1.72;">
              아닙니다. <b>AI-PLC 방법론을 워크샵에서 돌리기 위한 도구</b>입니다. 고객에게 파는 것은 방법론이고, 도구는 그것을 반나절에 완주하게 하는 수단입니다.
            </td>
          </tr>
        </tbody>
      </table>
    </td>
  </tr>

  <!-- ===================== CTA ===================== -->
```

- [ ] **Step 7: 푸터**

`281`행:
```html
        <b>Pathfinder</b> · From Pain Point to Validated Prototype &mdash; AI-PLC Discovery 방법론을 대화형 캔버스로 구동하는 워크샵 플랫폼.<br>
```
바꿀 문자열:
```html
        <b>AWS AI-PLC</b>(AI-Driven Product Life Cycle) Discovery 핸즈온 &mdash; 방법론 원본 <span style="font-family:monospace;">github.com/aws-samples/sample-ai-plc</span> · 구동 도구 <b>Pathfinder</b>.<br>
```

- [ ] **Step 8: 검증**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① 이메일 제약
grep -n "<pre\|display: *flex\|display:flex\|gap:\|display: *grid\|<style" docs/workshop/pitch-internal.html || echo "OK: 이메일 금지 요소 없음"
# ② 약어 확장
grep -c "AI-Driven Product Life Cycle" docs/workshop/pitch-internal.html
grep -n "AI-Driven Development Life Cycle" docs/workshop/pitch-internal.html || echo "OK: Development 표기 없음"
# ③ FAQ 3문답이 모두 있는지
grep -c "리포를 받아서 직접 돌리면\|AI-PLC가 뭔가요\|Pathfinder는 AWS 제품인가요" docs/workshop/pitch-internal.html
# ④ Greenfield 근거
grep -c "Greenfield" docs/workshop/pitch-internal.html
# ⑤ & 이스케이프
grep -n "&[^a-z#]" docs/workshop/pitch-internal.html || echo "OK: & 이스케이프 정상"
```
Expected: ① "OK" ② `1` + "OK" ③ `3` ④ `1` 이상 ⑤ "OK"

- [ ] **Step 9: 렌더 확인 후 커밋**

`pitch-internal.html`을 열어 ① 라이프사이클 4칸 ② FAQ 6행(질문 3 + 답변 3)이 질문은 회색 배경, 답변은 흰 배경으로 교대하는지 ③ 마지막 답변 행에 하단 테두리가 없는지 확인한다.

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/pitch-internal.html
git commit -m "docs(workshop): 내부 피치를 AI-PLC 자산 논리로 재구성 + 고객 FAQ

AWS 공개 방법론이라는 점이 세일즈 논리의 핵심이 된다 — 고객에게
남는 것이 도구 종속이 아니고, 후속 논의(Inception/Construction)가
방법론 안에서 이어진다. '납품물이 아니다'의 근거를 자체 방어에서
방법론 규정으로 옮기고, 배제 조건에 Greenfield 규정을 붙였다.
SA/AM이 현장에서 받을 3가지 질문의 답변을 추가한다."
```

---

### Task 6: facilitator.html — 진행 대본

최소 변경. 진행자가 **현장에서 질문받았을 때 답할 수 있는 것**만 넣는다.

**Files:**
- Modify: `docs/workshop/facilitator.html:250-252` (hero 킥커·제목·리드)
- Modify: `docs/workshop/facilitator.html:686-687` (게이트 섹션 리드)
- Modify: `docs/workshop/facilitator.html:699-716` (게이트 표의 stage 이름)
- Modify: `docs/workshop/facilitator.html:729-730` (트러블슈팅 리드) 및 FAQ 블록 신규

**Interfaces:**
- Consumes: Task 5가 확정한 FAQ 3문답 문안. **동일 문안을 쓴다** — SA/AM과 진행자가 같은 답을 해야 한다.
- Consumes: Task 1의 hero 킥커 패턴.

**제약**: 이 파일은 `<style>` 블록 CSS를 쓴다(이메일이 아니다). 기존 클래스만 재사용한다.

- [ ] **Step 1: hero에 AI-PLC 좌표**

`250-252`행:
```html
      <p class="kicker">Facilitator Runbook · 진행자 전용 · 참가자에게 띄우지 않습니다</p>
      <h1><span class="gr">Pathfinder 워크샵</span><br>운영 대본</h1>
      <p>반일 핸즈온(Path A)의 당일 진행 대본입니다. 단계마다 <b>화면에서 무엇이 일어나는지 · 진행자가 개입할 지점 · 흔한 막힘</b>을 정리했습니다. 참가자용 소개자료는 <code>intro.html</code>입니다.</p>
```
바꿀 문자열:
```html
      <p class="kicker">Facilitator Runbook · 진행자 전용 · 참가자에게 띄우지 않습니다</p>
      <h1><span class="gr">AI-PLC Discovery 워크샵</span><br>운영 대본</h1>
      <p>반일 핸즈온(Path A)의 당일 진행 대본입니다. 오늘 도는 것은 <b>AI-PLC</b>(AI-Driven Product Life Cycle, <code>aws-samples/sample-ai-plc</code>)의 <b>Discovery</b> 구간이고, 구동 도구가 Pathfinder입니다. 단계마다 <b>화면에서 무엇이 일어나는지 · 진행자가 개입할 지점 · 흔한 막힘</b>을 정리했습니다. 참가자용 소개자료는 <code>intro.html</code>입니다.</p>
```

- [ ] **Step 2: 게이트 섹션 — 게이트가 룰 규정임을 명시**

`687`행:
```html
      <p class="lead">참가자가 오늘 배워야 할 단 하나를 고른다면: <b>AI가 만든 것을 사람이 판단하는 경험</b>입니다. 게이트가 그 순간입니다.</p>
```
바꿀 문자열:
```html
      <p class="lead">참가자가 오늘 배워야 할 단 하나를 고른다면: <b>AI가 만든 것을 사람이 판단하는 경험</b>입니다. 게이트가 그 순간입니다.</p>
      <p>게이트는 우리가 워크샵용으로 넣은 장치가 아니라 <b>AI-PLC 룰의 규정</b>입니다. 상류 룰에는 과신 방지(<code>overconfidence-prevention.md</code>) 항목이 따로 있고, <b>&ldquo;확실하지 않으면 물어라 &mdash; 과신은 나쁜 결과로 이어진다&rdquo;</b>를 원칙으로 명시합니다. 진행자가 대신 승인하는 것은 편법이 아니라 <b>방법론을 어기는 것</b>입니다.</p>
```

- [ ] **Step 3: 게이트 표의 stage 이름 병기**

`699`, `703`, `707`, `711`, `715`행의 `<span class="s">` 안 이름에 영문 stage를 병기한다.

`699`행:
```html
            <td><span class="s">페인 포인트 분석</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Envision</span><br><span style="font-size:12px; color:var(--muted)">페인 포인트 분석</span></td>
```

`703`행:
```html
            <td><span class="s">PR/FAQ</span> <span class="pill gate">핵심</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Envision</span> <span class="pill gate">핵심</span><br><span style="font-size:12px; color:var(--muted)">PR/FAQ</span></td>
```

`707`행:
```html
            <td><span class="s">솔루션 선택</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Solution Analysis</span><br><span style="font-size:12px; color:var(--muted)">솔루션 선택</span></td>
```

`711`행:
```html
            <td><span class="s">프로토타입 명세</span> <span class="pill hot">범위 관리</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Prototype Spec</span> <span class="pill hot">범위 관리</span><br><span style="font-size:12px; color:var(--muted)"><code>PROTOTYPE-*.md</code></span></td>
```

`715`행:
```html
            <td><span class="s">Discovery 문서</span></td>
```
바꿀 문자열:
```html
            <td><span class="s">Discovery Document</span><br><span style="font-size:12px; color:var(--muted)">최종 산출물</span></td>
```

- [ ] **Step 4: 트러블슈팅 섹션에 FAQ 블록 신규**

`728-730`행의 섹션 시작 부분에 방법론 질문 FAQ를 **기존 증상 표보다 먼저** 넣는다. Task 5와 동일한 3문답 문안을 쓰되, 진행자용이므로 `.note info` 클래스로 감싼다.

찾을 문자열:
```html
      <h2>트러블슈팅 · FAQ <span class="badge warm">현장 대응</span></h2>
      <p class="lead">증상 → 원인 → 대처. <b>위쪽이 더 자주 발생</b>하는 순서입니다.</p>
```
바꿀 문자열:
```html
      <h2>트러블슈팅 · FAQ <span class="badge warm">현장 대응</span></h2>

      <h3>참가자가 방법론에 대해 물을 것</h3>
      <p class="lead">기술 장애보다 이 질문들이 먼저 나옵니다. <b>SA/AM 자료와 같은 답</b>을 쓰세요.</p>

      <table>
        <thead><tr><th>질문</th><th>이렇게 답하세요</th></tr></thead>
        <tbody>
          <tr>
            <td><b>"리포를 받아서 직접 돌리면 되지 않나요?"</b></td>
            <td>돌릴 수 있습니다 — <b>Claude Code나 Kiro를 쓸 줄 아는 사람이라면.</b> 상류 룰은 질문을 채팅이 아니라 <code>*-questions.md</code> 파일에 넣고 <code>[Answer]:</code> 태그 뒤에 답하도록 규정합니다. 오늘 참가자에게 필요한 것은 방법론이지 터미널이 아닙니다. Pathfinder는 <b>그 간극만</b> 메웁니다.</td>
          </tr>
          <tr>
            <td><b>"AI-PLC가 뭔가요?"</b></td>
            <td>AWS가 공개한 AI 기반 제품 라이프사이클 방법론입니다(<code>aws-samples/sample-ai-plc</code>). Discovery → Inception → Construction → Operations 네 구간이고, <b>오늘은 첫 구간인 Discovery</b>를 돕니다.</td>
          </tr>
          <tr>
            <td><b>"Pathfinder는 AWS 제품인가요?"</b></td>
            <td>아닙니다. <b>AI-PLC 방법론을 돌리기 위한 도구</b>입니다. 오늘 배워 가는 것은 방법론이고, 도구는 그것을 반나절에 완주하게 하는 수단입니다.</td>
          </tr>
          <tr>
            <td><b>"워크샵 끝나면 못 쓰는 거죠?"</b></td>
            <td>환경은 정리되지만 <b>방법론은 공개돼 있습니다.</b> 산출물 zip을 받아 가면 사내에서 같은 절차를 이어서 돌릴 수 있습니다. <b>zip 다운로드를 반드시 챙기게 하세요</b> — 이 답을 하려면 산출물이 손에 있어야 합니다.</td>
          </tr>
        </tbody>
      </table>

      <h3>기술 장애</h3>
      <p class="lead">증상 → 원인 → 대처. <b>위쪽이 더 자주 발생</b>하는 순서입니다.</p>
```

- [ ] **Step 5: 검증**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
# ① 약어 확장
grep -c "AI-Driven Product Life Cycle" docs/workshop/facilitator.html
grep -n "AI-Driven Development Life Cycle" docs/workshop/facilitator.html || echo "OK: Development 표기 없음"
# ② FAQ 4문답
grep -c "리포를 받아서 직접 돌리면\|AI-PLC가 뭔가요\|Pathfinder는 AWS 제품인가요\|워크샵 끝나면 못 쓰는" docs/workshop/facilitator.html
# ③ 게이트가 룰 규정이라는 근거
grep -c "overconfidence-prevention" docs/workshop/facilitator.html
# ④ 새 CSS 규칙을 추가하지 않았는지
git diff docs/workshop/facilitator.html | grep "^+" | grep -c "^\+\s*\.\w" || echo "OK: CSS 클래스 추가 없음"
```
Expected: ① `1` + "OK" ② `4` ③ `1` ④ "OK: CSS 클래스 추가 없음"

- [ ] **Step 6: 렌더 확인 후 커밋**

`facilitator.html`을 열어 ① hero 제목이 두 줄로 정상 표시되는지 ② 게이트 표의 stage 셀이 2줄로 깨지지 않는지 ③ 트러블슈팅에 표가 2개(방법론 FAQ + 기술 장애) 생겼고 `<h3>` 두 개로 구분되는지 확인한다.

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/facilitator.html
git commit -m "docs(workshop): 진행 대본에 AI-PLC 좌표와 방법론 FAQ 추가

게이트를 대신 누르지 말라는 지침에 근거가 생겼다 — 상류 룰의
overconfidence-prevention 규정이다. 참가자가 물을 4가지 질문의
답을 SA/AM 자료와 동일 문안으로 넣고, 게이트 표의 stage 이름을
화면에 뜨는 영문명과 맞췄다."
```

---

### Task 7: 4개 파일 교차 검증

각 태스크가 개별 검증을 통과했어도 파일 간 불일치가 남을 수 있다. 스펙 §검증의 5개 항목을 전체 파일에 대해 한 번에 확인한다.

**Files:**
- Modify: (검증 실패 시에만) 해당 파일

**Interfaces:**
- Consumes: Task 1–6의 모든 산출물.

- [ ] **Step 1: 약어 확장 통일 — 전체 파일**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
echo "=== Development 표기 (0이어야 함) ==="
grep -rn "AI-Driven Development Life Cycle" docs/workshop/ || echo "OK: 없음"
echo "=== Product 표기 파일별 ==="
for f in docs/workshop/*.html; do echo "$f: $(grep -c 'AI-Driven Product Life Cycle' $f)"; done
```
Expected: Development는 "OK: 없음". Product는 `edm.html`·`intro.html`·`pitch-internal.html`·`facilitator.html` 각각 `1` 이상.

- [ ] **Step 2: 첫 등장 규칙 — 파일별로 풀네임과 링크가 첫 등장 근처에 있는지**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
for f in docs/workshop/*.html; do
  echo "=== $f ==="
  first=$(grep -n "AI-PLC" $f | head -1 | cut -d: -f1)
  echo "첫 등장: ${first}행"
  full=$(grep -n "AI-Driven Product Life Cycle" $f | head -1 | cut -d: -f1)
  echo "풀네임: ${full}행"
  grep -c "aws-samples/sample-ai-plc" $f | xargs echo "리포 URL 횟수:"
done
```
Expected: 각 파일에서 리포 URL이 `1` 이상. 풀네임 행이 첫 등장 행과 같거나 근처(±30행)여야 한다. 네비/메뉴/title처럼 목차 성격의 첫 등장은 예외로 허용한다 — 본문 첫 등장에 풀네임이 있으면 된다.

- [ ] **Step 3: Phase / Stage 혼용 — 전체 파일**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
echo "=== stage 이름에 단계/phase 오용 ==="
grep -rn "Envision 단계\|Envision phase\|Solution Analysis 단계\|Solution Analysis phase\|Go-to-Market 단계\|Product Strategy 단계\|Prototype & Validation 단계" docs/workshop/ || echo "OK: 없음"
echo "=== phase 이름에 stage 오용 ==="
grep -rn "Discovery stage\|Inception stage\|Construction stage\|Operations stage" docs/workshop/ || echo "OK: 없음"
```
Expected: 두 "OK: 없음".

- [ ] **Step 4: 정직성 제약 — 무수정 주장이 없는지**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
grep -rn "무수정\|수정 없이\|손대지 않고\|원본 그대로 실행\|룰을 그대로 싣" docs/workshop/ || echo "OK: 무수정 주장 없음"
echo "=== 허용 표현이 쓰였는지 (intro 카드 1번) ==="
grep -n "진행 언어만" docs/workshop/intro.html
```
Expected: "OK: 무수정 주장 없음" + `intro.html`에서 "진행 언어만" 히트 `1`.

- [ ] **Step 5: 이메일 제약 — edm과 pitch**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
for f in docs/workshop/edm.html docs/workshop/pitch-internal.html; do
  echo "=== $f ==="
  grep -n "<pre\|display: *flex\|display:flex\|gap:\|display: *grid\|display:grid\|<style" $f || echo "OK: 금지 요소 없음"
done
echo "=== 벌거벗은 & (이스케이프 누락) ==="
grep -rn "&[^a-zA-Z#]" docs/workshop/edm.html docs/workshop/pitch-internal.html || echo "OK: & 정상"
```
Expected: 각 파일 "OK: 금지 요소 없음" + "OK: & 정상".

- [ ] **Step 6: HTML 구조 무결성 — 태그 균형**

신규 블록을 삽입했으므로 테이블 태그 균형이 깨졌는지 확인한다.

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
for f in docs/workshop/*.html; do
  echo "=== $f ==="
  echo "  <tr> $(grep -o '<tr' $f | wc -l) / </tr> $(grep -o '</tr>' $f | wc -l)"
  echo "  <td> $(grep -o '<td' $f | wc -l) / </td> $(grep -o '</td>' $f | wc -l)"
  echo "  <table> $(grep -o '<table' $f | wc -l) / </table> $(grep -o '</table>' $f | wc -l)"
  echo "  <div> $(grep -o '<div' $f | wc -l) / </div> $(grep -o '</div>' $f | wc -l)"
done
```
Expected: 각 파일에서 여는 태그 수 == 닫는 태그 수. 불일치가 있으면 해당 파일의 신규 삽입 블록을 다시 확인한다.

Python으로 더 확실하게 검증할 수도 있다:
```bash
cd /home/ec2-user/project/pathfinder-sp
python3 -c "
from html.parser import HTMLParser
import sys
VOID={'br','img','meta','link','input','hr','area','base','col','embed','source','track','wbr'}
class P(HTMLParser):
    def __init__(s): super().__init__(); s.stack=[]; s.err=[]
    def handle_starttag(s,t,a):
        if t not in VOID: s.stack.append((t,s.getpos()[0]))
    def handle_endtag(s,t):
        if t in VOID: return
        if not s.stack: s.err.append(f'{s.getpos()[0]}: 여는 태그 없이 </{t}>'); return
        top,ln=s.stack.pop()
        if top!=t: s.err.append(f'{s.getpos()[0]}: </{t}> 인데 열린 것은 <{top}> ({ln}행)')
for f in ['edm.html','intro.html','pitch-internal.html','facilitator.html']:
    p=P()
    p.feed(open('docs/workshop/'+f,encoding='utf-8').read())
    unclosed=[f'<{t}>({l}행)' for t,l in p.stack]
    print(f'{f}: 오류 {len(p.err)}개, 미닫힘 {len(unclosed)}개')
    for e in p.err[:5]: print('   ', e)
    for u in unclosed[:5]: print('    미닫힘', u)
"
```
Expected: 4개 파일 모두 오류 0개, 미닫힘 0개. (기존 파일에 원래 있던 불균형이라면 `git stash`로 대조해 신규 삽입 탓인지 가려낸다.)

- [ ] **Step 7: 브라우저 육안 확인 — 4개 전부**

Run: `python3 -m http.server 8899 --directory docs/workshop &`

각 파일을 열어 확인한다:
- `http://localhost:8899/intro.html` — 라이프사이클 4칸, §3 대조표, §5 표의 stage 셀
- `http://localhost:8899/edm.html` — 라이프사이클 테이블 4칸 균등, 🎯 박스
- `http://localhost:8899/pitch-internal.html` — 라이프사이클, FAQ 6행 교대 배경
- `http://localhost:8899/facilitator.html` — hero, 게이트 표, FAQ 표 2개

창 폭을 좁혀 반응형도 확인한다. 확인 후: `kill %1`

- [ ] **Step 8: 스펙 대조 — 모든 변경 항목이 반영됐는지**

Run:
```bash
cd /home/ec2-user/project/pathfinder-sp
git log --oneline main..HEAD
git diff --stat main..HEAD -- docs/workshop/
```
스펙 `2026-08-03-aiplc-positioning-design.md`의 "자료별 변경 범위" 4개 표를 열어 각 행이 실제 변경에 반영됐는지 하나씩 대조한다. 누락이 있으면 해당 파일을 수정하고 커밋한다.

- [ ] **Step 9: 검증 결과 커밋 (수정이 있었을 경우에만)**

교차 검증에서 수정한 것이 있으면 커밋한다. 없으면 이 Step을 건너뛴다.

```bash
cd /home/ec2-user/project/pathfinder-sp
git add docs/workshop/
git commit -m "docs(workshop): 4개 자료 교차 검증 — 용어·태그 정합성 수정"
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 항목 | 태스크 |
|---|---|
| 핵심 서사 (AI-PLC 주인공 / Pathfinder 실행체) | 1(hero), 2(§3 전면) |
| 대조표 5행 | 2 Step 2 |
| 용어 정합성 ① 약어 Product | Global Constraints + 1·4·5·6 검증 + 7 Step 1 |
| 용어 정합성 ② Phase/Stage | Global Constraints + 3 Step 8 + 7 Step 3 |
| 용어 정합성 ③ `PROTOTYPE-*.md` 노출 | 3 Step 2·4 |
| 용어 정합성 ④ 첫 등장 규칙 | 1 Step 2, 4 Step 2, 5 Step 1, 6 Step 1 + 7 Step 2 |
| 정직성 제약 (무수정 주장 금지) | Global Constraints + 2 Step 3·4 + 7 Step 4 |
| 라이프사이클 4-phase 전체 노출 | 1 Step 5(intro), 4 Step 3(edm), 5 Step 3(pitch), 6 Step 1(facilitator 한 줄) |
| edm.html 변경 표 7행 | 4 Step 1–5 |
| intro.html 변경 표 10행 | 1, 2, 3 |
| pitch-internal.html 변경 표 7행 | 5 Step 1–7 |
| facilitator.html 변경 표 4행 | 6 Step 1–4 |
| FAQ 3문답 | 5 Step 6(원본), 6 Step 4(+1문답 추가) |
| 검증 5개 항목 | 7 Step 1–7 |

`facilitator.html`에 4번째 문답("워크샵 끝나면 못 쓰는 거죠?")을 추가한 것은 스펙에 없던 것이다. 진행자가 현장에서 가장 자주 받을 질문이고 스펙의 "방법론은 공개 자산" 논지와 직결되므로 포함했다. 스펙 범위의 확장이 아니라 같은 논지의 적용이다.

**2. 플레이스홀더 스캔** — 모든 Step에 실제 찾을/바꿀 문자열이 들어 있다. `grep`으로 대상을 먼저 찾아보라고 미룬 Step은 없다.

**3. 타입/문안 일관성** — 라이프사이클 4칸 문안, FAQ 3문답, 대조표 문구가 태스크 간에 동일한지 확인했다. `Prototype & Validation`은 HTML에서 `Prototype &amp; Validation`으로 이스케이프한다(Task 3 Step 5, Task 4 Step 4에 명시).

---

## Execution Handoff

계획을 `docs/superpowers/plans/2026-08-03-aiplc-positioning.md`에 저장했다.
