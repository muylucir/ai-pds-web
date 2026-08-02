# Pathfinder 통합 규약 (UI 접점 — 반드시 준수)

이 파일은 Pathfinder 웹 UI와의 접점만 규정한다. Discovery 워크플로우 자체는
작업 디렉터리의 `CLAUDE.md`(AI-PLC core-workflow)를 따른다.

- 사용자에게 객관식 질문을 할 때는 반드시 **AskUserQuestion** 도구를 사용한다.
  질문 파일(aiplc-docs/**-questions.md)은 기록용으로 계속 작성하되, 질문 전달
  자체는 도구로만 한다.
- 스테이지를 시작/완료할 때마다 **report_stage** 도구를 호출한다. 이 도구가
  aiplc-state.md를 자동 갱신하므로 상태 파일을 직접 만들 필요 없다.
- discovery-document를 생성/갱신할 때마다 **submit_document** 도구를 호출한다.
  **순서가 중요하다: 반드시 파일을 저장한 뒤에 submit_document를 호출한다.**
  파일이 없거나 비어 있으면 도구가 선언을 거부하고 그 이유를 돌려준다 — 그
  경우 파일 저장부터 다시 하라는 뜻이다.
- audit.md에 엔트리를 추가할 때는 **Edit**으로 append한다. **Write는 파일
  전체를 덮어쓴다** — 새 엔트리만 담아 Write를 호출하면 기존 감사 기록이 전부
  유실된다.

## 문서 양식의 영어 문구는 번역해서 쓴다 (상류 룰 오버라이드)

상류 룰의 문서 양식에는 **완성된 영어 문장**이 리터럴로 박혀 있다. 대표적으로
`envision.md`의 PR/FAQ 질문들이다:

```markdown
#### Q: What is the price?
A: [Answer]
```

`A:` 쪽은 `[Answer]`라는 빈 자리지만 `Q:` 쪽은 이미 영어로 완성돼 있어서, 그대로
복사하면 **질문은 영어, 답변은 한국어**인 문서가 나온다. 실제로 그렇게 나왔다:
템플릿에 있던 질문 20여 개는 영어로 남고, 에이전트가 직접 추가한 질문 하나만
한국어였다.

원인은 두 지시가 반대를 말하기 때문이다 — core-workflow의 "모든 문서작성은
한국어"와, 템플릿 바로 앞의 `**CRITICAL**: Use the ... format exactly as defined
below. Do NOT deviate from this structure.` 후자가 더 강조돼 있고 맥락도 가까워
이긴다. **그 CRITICAL은 이렇게 읽어야 한다:**

- **"exactly as defined"가 요구하는 것은 구조다** — 섹션 순서, 항목 구성, 어느
  질문이 들어가는지, 계층(`####`)과 `Q:`/`A:` 표기. 이것은 바꾸지 않는다.
- **언어는 구조가 아니다.** 질문 문구·헤딩·라벨은 **한국어로 번역해서 쓴다.**
  질문을 빼거나 순서를 바꾸거나 새로 만들라는 뜻이 아니다 — 같은 질문을 한국어로
  적으라는 뜻이다.

적용 대상은 PR/FAQ만이 아니다. `product-strategy.md`, `go-to-market.md`에도 같은
형태의 영어 리터럴이 있고(각각 십수 개), 같은 규칙을 적용한다. 즉 **상류 룰의
양식에서 가져온 모든 사용자 노출 문구는 한국어로 옮긴다.**

영어를 그대로 두는 것은 core-workflow가 예외로 둔 것뿐이다 — **기술용어·고유명사·
파일명**, 그리고 경로·도구 이름·코드 식별자. 예를 들어 `PROTOTYPE-{slug}.md`,
`offline-first`, `TAM`, `SaaS`는 그대로 두고, `Q: What is the price?`는
`Q: 가격은 어떻게 책정되나요?`로 적는다.

문서의 **섹션 헤딩도 같다**(`### Press Release` → `### 보도자료`,
`### External FAQs (Customer-Facing)` → `### 외부 FAQ (고객 대상)`). 단
`submit_document`가 파싱에 의존하는 파일명과 경로는 절대 번역하지 않는다.

## 프로토타입: 스펙만 쓰고 빌드하지 않는다 (상류 룰 오버라이드)

Pathfinder에서 프로토타입 **빌드와 실행은 프로토타입 탭이 전담한다**. 전용
호스팅 계층(`ProtoHost`)만이 포트를 할당하고 프리뷰 프록시에 등록할 수 있으므로,
여기서 직접 띄운 서버는 화면에 나타나지도, 프리뷰 링크로 열리지도 않는다.
Discovery의 역할은 **스펙 문서 작성까지**다.

- `aws-aiplc-rule-details/discovery/prototype-md-format.md`를 따라
  `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`를 작성한다. 이
  경로 규약이 프로토타입 탭의 카드 목록 기준이다 — 벗어나면 카드가 뜨지 않는다.
- `aws-aiplc-rule-details/discovery/prototype-building.md`의 빌드 단계는
  **수행하지 않는다**. 그 룰은 사람이 로컬에서 직접 돌리는 상류 워크숍 전제로
  쓰였다. 구체적으로 다음을 하지 않는다:
  - `npm install` / `npm run build` / `npm run dev` 등 빌드·실행 명령
  - 프로토타입 서브프로세스 실행 (자격증명 격리 지침도 적용 대상이 없다)
  - "Deploying to…" / "Running at http://localhost:{port}" 류의 진행·완료 보고
- **포트를 정하지 않는다.** 상류 룰의 `Port: {3000 + X}`와 스펙 양식의 `Port`
  항목은 Pathfinder에서 무효다 — 포트는 빌드 시점에 호스팅이 배정한다. 스펙에
  포트를 적으면 실제 배정값과 어긋나 사용자를 오도한다.
- 스펙을 저장한 뒤에는 **프로토타입 탭에서 빌드하라고 안내하며 턴을 마친다.**
  Discovery 채팅에서 빌드가 시작될 것처럼 말하지 않는다.

## 대화 진행 (사용자 화면에 반드시 노출)

- 도구만 호출하고 끝내지 말 것. **모든 턴에서 사용자에게 보일 대화 텍스트를
  함께 작성한다** — 도구를 호출하기 전에는 지금 무엇을 왜 하는지 한두 문장으로
  알리고, 턴을 마칠 때는 무엇을 했고 다음에 무엇을 요청/기대하는지 요약한다.
  채팅 말풍선은 이 텍스트로 채워진다. 텍스트 없이 도구 호출만 있는 턴은
  사용자에게 빈 말풍선으로 보이므로 금지한다.
- AskUserQuestion으로 질문을 전달하는 턴에서도, 질문 폼을 띄우기 전에 왜 이
  질문이 필요한지 한 문장으로 먼저 설명한다.
