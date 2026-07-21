# 대시보드 상태 영속화 + 워크스페이스/리뷰 UX 개선 설계

날짜: 2026-07-22
상태: 승인됨 (구현 대기)

## 배경

스크린샷(files/iShot_2026-07-22_08.14.22.png)의 프로젝트(qa-test)에서 확인된 문제와
사용자 요청 개선 4건. 근본 진단: 해당 프로젝트 S3에 `aiplc-docs/aiplc-state.md`가
아예 없다 — 방법론 룰은 스테이지 완료마다 상태 파일 갱신을 명시하지만 이는 프롬프트
규약일 뿐 코드가 강제하지 않아, 에이전트가 `report_stage` 도구만 호출하고 파일 쓰기를
건너뛰면 대시보드(진행률·타임라인)·목록 진행상황·승인 게이트 배지가 전부 빈다.

## 항목 0: report_stage가 aiplc-state.md를 코드로 보장

**결정(사용자)**: 상태 영속화를 모델 선의에 맡기지 않고 `report_stage` 도구가
기계적으로 upsert한다.

- `backend/pathfinder/agent/tools.py`의 `report_stage(stage, status, summary)`가
  이벤트 emit에 더해 **워크스페이스의 `aiplc-docs/aiplc-state.md`를 갱신**한다.
- upsert 로직 (`pathfinder/agent/state_sync.py` 신설, 순수 함수로 테스트):
  - 파일이 없으면 최소 골격 생성: `# AI-PLC State` 헤더 + `- **Current Stage**: {stage}` +
    `## Stage Progress` 체크리스트(이 스테이지 1줄).
  - 파일이 있으면: 해당 스테이지 줄을 찾아(`- [ ]`/`- [x]` + 이름 매칭, 기존
    `parse_state_file`의 이름 매칭과 동일하게 부분 일치 허용) status에 따라 체크박스
    갱신(completed → `[x]`, 그 외 → `[ ]`). 줄이 없으면 `## Stage Progress` 끝에 추가.
    `- **Current Stage**:` 줄은 in_progress/pending이면 해당 스테이지명으로 갱신
    (completed는 Current Stage를 건드리지 않음 — 다음 in_progress가 갱신).
  - 결과물은 기존 `parse_state_file`이 정상 파싱하는 포맷이어야 한다(왕복 테스트).
- 도구 실패 격리: 상태 파일 upsert 실패(IO 등)는 report_stage 반환을 실패시키지
  않고 로그만 남긴다 — 이벤트 emit(화면)은 항상 성공. (fail-soft)
- 시스템 프롬프트 addendum(driver의 `_CONTACT_ADDENDUM`)에 한 줄 보강: report_stage가
  상태 파일을 자동 갱신하므로 별도로 file_write로 aiplc-state.md를 만들 필요 없음
  (중복 작성 방지).

## 항목 1: 워크스페이스 문서 패널 드롭다운

- `WorkspaceDocPanel` 헤더의 고정 문서명을 **드롭다운(`<select aria-label="문서 선택">`)**
  으로 바꾼다. 옵션 = `listArtifacts(projectId)`(aiplc-docs/** 경로 목록), 표시
  텍스트는 파일명(경로 꼬리), value는 전체 경로. 5초 폴링 없이 turnSeq 변화 시 재조회
  (턴이 끝나면 새 문서가 목록에 반영).
- 선택 우선순위: 사용자가 드롭다운으로 고르면 그 선택 유지. 새 `submit_document`/문서
  이벤트(activeDoc 변경)가 오면 **자동으로 그 문서로 전환**하되, 이는 "사용자 선택"을
  덮는다(대화가 다루는 문서를 따라가는 기존 동작 유지 — 단, 이벤트가 온 경우에만).
- 목록이 비고 activeDoc도 없으면 기존 빈 상태 문구 유지.

## 항목 2: 산출물 zip 다운로드 (문서 리뷰)

- 백엔드 신설: `GET /projects/{pid}/artifacts/archive` → `application/zip` 바이너리.
  - S3에서 `aiplc-docs/` 프리픽스 전체(list → get 병렬)를 Python stdlib `zipfile`로
    인메모리 zip(각 엔트리 키 = 워크스페이스 상대 경로). 외부 의존성 없음.
  - 파일명 헤더: `Content-Disposition: attachment; filename="{pid}-artifacts.zip"`.
  - redaction: `aiplc-docs/audit.md`는 S3에 이미 redacted 저장(기존 sync 정책)이므로
    추가 처리 없음. 산출물 없으면 빈 zip이 아니라 404.
  - 미등록 프로젝트 404(`ensure_workspace`와 동일한 등록 확인 — 단 워크스페이스
    초기화는 불필요하므로 registry 등록 여부만 확인).
- 프론트: 문서 리뷰 화면 우측 상단(.md 다운로드 옆)에 "전체 다운로드 (.zip)" 버튼 —
  `<a href={API}/projects/{pid}/artifacts/archive>` 방식이 아니라 기존 fetch 헬퍼로
  blob 다운로드(인증 헤더 경로 유지).

## 항목 3: 채팅 자동 스크롤

현재 smart autoscroll은 "바닥 120px 이내일 때만" 따라간다 — 긴 응답이 스트리밍되면
바닥에서 멀어져 따라가기가 중단되고, 사용자가 매번 수동 스크롤해야 한다.

- `components/canvas/ChatTimeline.tsx` 스크롤 정책 변경:
  - **stick-to-bottom 상태**를 명시적으로 관리: 기본 true. 사용자가 위로 스크롤
    (wheel up/touchmove/scrollTop이 바닥에서 충분히 멀어짐)하면 false, 사용자가
    바닥 근처로 되돌아오면 true.
  - stick=true인 동안 items 변경(스트리밍 청크 포함)마다 `scrollTop = scrollHeight`.
  - **내가 메시지를 보내면 무조건 stick=true로 리셋**하고 바닥으로 — "답변을 입력하면
    매번 수동 스크롤" 문제의 직접 해결. (send/submitAnswers 경로 모두)
  - 기존 주의사항 유지: `scrollIntoView` 금지(문서 스크롤 오염), 컨테이너 scrollTop만.
- 사용자 위로-스크롤 의도 감지는 `scroll` 이벤트에서 "이전 scrollTop보다 감소 +
  바닥에서 멀어짐"으로 판정(프로그램적 스크롤과 구분).

## 항목 4: 수정 요청 → 워크스페이스 채팅으로 이동

**결정(사용자)**: 인라인 폼/모달 대신 워크스페이스 채팅으로 이동. 근거: 수정 요청은
대화형 턴(에이전트가 되물을 수 있음 — ask_questions)이고, 리뷰 화면에는 질문 폼·
스트리밍 UI가 없다. 현재 증상("눌러도 무반응")의 원인은 폼이 페이지 상단(게이트
아래)에 렌더되어 스크롤 위치에 따라 뷰포트 밖에 열리는 것.

- `ApprovalGate`의 "수정 요청" 버튼: 인라인 폼 토글 제거 →
  `/projects/{pid}/workspace?draft={인코딩된 초안}`으로 라우팅. 초안 텍스트:
  `"{문서 파일명} 수정 요청: "` (예: `discovery-document.md 수정 요청: `).
- 워크스페이스: `?draft=` 쿼리 파라미터가 있으면 채팅 입력창에 프리필 + 포커스
  (전송은 하지 않음 — 사용자가 내용을 이어 쓰고 보냄). 처리 후 URL에서 draft 제거
  (replaceState — 새로고침 시 재프리필 방지).
- "승인" 버튼은 리뷰 화면에 유지(고정 메시지 1회, 문서를 보며 확정하는 맥락).
  ApprovalGate 설명 문구를 새 흐름에 맞게 갱신: 수정 요청은 워크스페이스 채팅으로
  이동해 AI와 대화로 진행한다고 명시.
- `onRevise`/인라인 textarea 관련 코드·테스트 제거.

## 테스트

- 항목 0: `state_sync` 순수 함수 유닛(파일 없음→골격 생성 / 기존 스테이지 체크 갱신 /
  신규 스테이지 추가 / Current Stage 갱신 규칙 / parse_state_file 왕복). tools.py
  통합(report_stage 호출 → 워크스페이스에 파일 생성됨, upsert 실패해도 반환 성공).
- 항목 1: 드롭다운 렌더(옵션=artifacts)·선택 시 해당 문서 로드·activeDoc 이벤트 시
  자동 전환.
- 항목 2: 백엔드 — zip 응답 200 + 엔트리 목록 = aiplc-docs 키, 빈 프로젝트 404,
  미등록 404. 프론트 — 버튼 클릭 → blob 다운로드 트리거(jsdom에서 createObjectURL 스파이).
- 항목 3: stick 상태 — 보내면 바닥 리셋, 스트리밍 append 시 바닥 유지, 사용자 위로
  스크롤 후 append 시 위치 보존. (jsdom 한계 내에서 scrollTop 조작으로 검증)
- 항목 4: 수정 요청 클릭 → router.push 경로/쿼리 검증, 워크스페이스 draft 프리필·
  포커스·URL 정리, 승인 버튼 동작 불변.

## 범위 제외 (YAGNI)

- 프로토타입/업로드 파일까지 zip에 포함 (요청은 "산출물" = aiplc-docs).
- 리뷰 화면에 스트리밍/질문 폼 이식 (수정 요청을 워크스페이스로 옮기는 것으로 해소).
- 상태 파일의 완전한 재구성(스테이지 순서 재정렬 등) — upsert는 최소 침습.

## Open Questions

없음.
