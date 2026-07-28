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
