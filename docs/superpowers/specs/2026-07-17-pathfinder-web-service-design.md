# Pathfinder — AI-PLC 웹 서비스 설계

**날짜**: 2026-07-17
**상태**: 승인됨
**대상 독자**: 구현 담당 개발자

## 배경과 목표

AI-PLC는 PM이 주도하는 Discovery 방법론이다. Workspace Detection → Discovery Mode Selection → Envision(PR/FAQ) → Solution Analysis → Prototype & Validation → Product Strategy → Go-to-Market을 거쳐 Discovery Document를 완성하고 개발자 워크스페이스로 핸드오프한다. 룰은 `files/aiplc-rules/`의 마크다운 파일이며, 실행 사례는 `files/pilot1/`(audit.md 41개 엔트리)에 기록되어 있다.

현재 가장 큰 걸림돌은 PM 같은 비개발자가 Kiro나 Claude Code(CLI)를 직접 다뤄야 한다는 점이다. Pathfinder는 이 방법론을 웹 서비스로 제공하여 접근성을 해결한다.

**1차 사용 맥락**: 고객 워크숍/엔게이지먼트 도구. 퍼실리테이터가 세션을 개설하고 고객 PM이 참여하는, 세션 단위 소수 테넌트 운영.

**핵심 설계 결정 (A안)**: 방법론을 백엔드 코드로 재작성하지 않는다. Lambda MicroVM 안에서 Claude Code(headless)에 aiplc-rules를 주입해 실행하고, 웹 백엔드는 얇은 중계층으로만 동작한다. 방법론의 실행 엔진은 pilot1에서 검증된 것을 그대로 쓰고, 룰은 데이터(마크다운)로 남아 재배포 없이 진화할 수 있다.

## 1. 전체 아키텍처

```
사용자 브라우저
   │
CloudFront ── 정적 자산 캐시 / API·SSE 경로는 캐시 제외
   │
EC2 (Graviton/arm64, 서울)
   ├─ 웹 백엔드 (thin): 세션 관리, 파일 브리지, SSE 중계
   ├─ traefik: /prototype/[id] → 프로토타입 컨테이너 라우팅
   └─ 프로토타입 컨테이너들 (승인된 것만, 격리 네트워크)
   │
Lambda MicroVM (프로젝트당 1개, 도쿄 리전 ap-northeast-1)
   ├─ Claude Code (headless) + aws-aiplc-rules 주입
   ├─ 하네스: 백엔드 ↔ Claude Code 중계용 HTTP 서버
   └─ 워크스페이스: aiplc-docs/, 프로토타입 소스
   │
S3 (서울): 아티팩트 영속화
ECR: 프로토타입 컨테이너 이미지
DynamoDB (서울): 프로젝트 메타데이터, 세션 상태
```

- **프로젝트 생성 = MicroVM 기동.** aiplc-rules + Claude Code + 하네스를 구운 MicroVM 이미지(Firecracker 스냅샷)에서 시작하므로 기동이 빠르다. 유휴 시 suspend(`autoResumeEnabled: true`), 사용자가 돌아오면 resume — Claude Code 세션과 워크스페이스가 그대로 유지된다.
- **Bedrock 인증은 MicroVM 실행 롤(IAM)로** 처리한다 (`CLAUDE_CODE_USE_BEDROCK`). 장기 API 키가 어디에도 존재하지 않아, audit.md 크리덴셜 레다크션 룰 준수가 구조적으로 보장된다.
- **Claude Code 모델은 Sonnet 5로 고정**한다 — MicroVM 이미지에 `ANTHROPIC_MODEL`을 Sonnet 5의 Bedrock cross-region inference profile(예: `global.anthropic.claude-sonnet-5`)로 설정. 정확한 프로파일 ID는 구현 시 `aws bedrock list-inference-profiles`로 확인한다(pilot1에서 모델 ID 불일치로 ValidationException을 겪은 전례 반영). 프로토타입이 사용하는 LLM 모델은 별개로, 방법론 룰(llm-model-configuration.md)에 따라 사용자에게 묻는다.
- 스택: Next.js 프론트엔드 + FastAPI 백엔드 + DynamoDB.

## 2. 핵심 프로토콜 — "파일이 곧 계약"

Claude Code가 룰대로 생성하는 파일을 UI 계약으로 사용한다.

| 방법론 산출물 | UI 렌더링 | 사용자 입력 처리 |
|---|---|---|
| `*-questions.md` | 질문 위저드 폼 (02) | `[Answer]: X` 태그에 기입 후 "완료" 메시지 전송 |
| `aiplc-state.md` | 대시보드 타임라인 (01) | — |
| `audit.md` | 최근 활동 피드 (01) | — |
| `discovery-document.md` | 문서 리뷰 탭 (03) | 승인 → "승인" 전송 / 수정 요청 → 자연어 전송 |
| `*-clarification-questions.md` | 모순 감지 배너 (02) | 선택지 → 답변 기입 후 전송 |
| 프로토타입 소스 | 캔버스 iframe 프리뷰 (04 변형) | 이터레이션 요청 → 자연어 전송 |

- **질문 파일 파서**: question-format-guide.md의 엄격한 형식(`## Question N`, `A)`–`F)` 옵션, 마지막 `X) Other`, `[Answer]:` 태그)에 기반해 파싱한다. **파싱 실패 시 원본 마크다운 렌더 + 자유 텍스트 입력 폼으로 폴백** — 어떤 경우에도 진행이 막히지 않는다.
- **모순 감지는 룰 원형대로 제출 후 일괄 검사.** clarification 파일이 생성되면 배너 UI로 렌더링한다. 실시간(작성 중) 감지는 이후 개선 사항.
- **하네스**: MicroVM 전용 HTTPS 엔드포인트로 노출되는 소형 HTTP 서버.
  - `POST /message` — 사용자 턴을 Claude Code에 전달
  - `GET /events` — 에이전트 출력·상태를 SSE로 스트리밍
  - `GET/PUT /files/*` — 워크스페이스 파일 읽기/쓰기 (답변 기입 등)
  - `/preview/*` — 프로토타입 로컬 개발 서버 리버스 프록시
  - 하트비트 — Claude Code 프로세스 생존 감시

## 3. 프로토타입 빌드 → 이터레이션 → 퍼블리시

pilot1에서 확인된 루프(빌드 → "추가할 기능이 생각났어" → 수정 → Playwright 검증 → 확인)를 그대로 재현한다.

1. **빌드**: Claude Code가 MicroVM 안에서 프로토타입을 빌드하고 로컬 개발 서버를 띄운다. 빌드 로그는 SSE로 캔버스에 스트리밍.
2. **이터레이션**: 캔버스 채팅으로 수정 요청 → Claude Code가 수정·Playwright 검증 → iframe 프리뷰(`/preview/*` 경유) 즉시 갱신. 컨테이너 빌드 없음.
3. **퍼블리시** (사용자 승인 시 1회): MicroVM 안에서 buildah로 arm64 이미지 빌드 → ECR 푸시 → EC2가 풀 → traefik 라우트 등록 → `https://도메인/prototype/[id]`. 신뢰할 수 없는 빌드가 서빙 호스트에서 실행되지 않는다.

**퍼블리시 컨테이너 하드닝**:
- 전용 docker 네트워크 — 백엔드·DB 접근 불가
- IMDS(169.254.169.254) 차단 — EC2 IAM 롤 크리덴셜 탈취 방지
- read-only 루트 FS, CPU/메모리 제한, capability drop
- 에이전틱(실 LLM 연동) 프로토타입을 서빙할 경우 크리덴셜을 컨테이너에 굽지 않고 프록시 경유

## 4. 수명 주기와 복구

- **아티팩트 동기화**: 에이전트 턴 종료마다 하네스가 `aiplc-docs/` + 프로토타입 소스를 S3(서울)로 동기화. 감사 추적이 MicroVM 수명과 무관하게 보존된다.
- **MicroVM 만료(최대 8시간)/장애**: 새 MicroVM 기동 → S3에서 워크스페이스 복원 → 방법론의 session-continuity 룰이 aiplc-state.md를 읽고 스스로 재개한다. 복구 로직은 룰이 이미 보유한 능력을 사용한다.
- **Claude Code 무응답**: 하네스 하트비트로 감지, UI에 상태 표시 + 턴 재시도.
- **suspend/resume**: 워크숍 중간 휴식(점심 등)에 suspend — 스냅샷 보관비만 발생, resume 시 세션 그대로 복원.

## 5. 화면 구성

기존 목업(`files/ui/01–04`) + 신규 4개. Discovery 단계는 위저드(01–03), 프로토타입 단계만 캔버스(04 변형) — 스테이지별 분리.

| 화면 | 출처 | 역할 |
|---|---|---|
| 프로젝트 목록/생성 | 신규 | 워크숍 세션 개설, 초대 링크 발급 |
| 대시보드 | 목업 01 | 스테이지 타임라인, 산출물 목록, 활동 피드(audit) |
| 질문 위저드 | 목업 02 | 질문 폼, AI 추천 기본값(★), 모순 배너, Other 자유 입력 |
| 문서 리뷰 | 목업 03 | Living Document 탭, 승인 게이트, 자연어 수정 요청 |
| 빌드/이터레이션 캔버스 | 목업 04 변형 | 좌 진행 사이드바 · 중앙 채팅 · 우측 iframe 프리뷰(아티팩트 패널 교체) |
| 빌드 진행 상태 | 신규(캔버스 내) | Claude Code 빌드 로그 스트리밍, 검증 결과 표시 |
| 핸드오프 | 신규 | Discovery Document·PROTOTYPE-*.md·전체 아카이브 다운로드, 퍼블리시 관리 |
| 세션 관리 | 신규 | 퍼실리테이터용: 프로젝트 상태, MicroVM suspend/resume, 비용 |

**인증**: 워크숍 모델 — 퍼실리테이터가 프로젝트 생성 → 토큰 기반 초대 링크로 고객 PM 참여. SSO는 이후 단계.

**목업 대비 변경점**: 02의 인라인(작성 중) 모순 감지는 제출 후 일괄 검사로 시작한다.

## 6. 데이터 거버넌스와 비용

- MicroVM은 도쿄 리전(서울 미지원, 2026-07 기준) — 고객 문서가 일시적으로 해외 리전에서 처리됨을 워크숍 시작 시 고지한다. 영속 저장(S3·DynamoDB)은 서울.
- 비용 구조: MicroVM은 활성 시간만 과금(suspend 시 스냅샷 보관비) + Bedrock 사용량. 프로젝트당 예상 비용은 구현 초기에 실측하여 세션 관리 화면에 노출한다.

## 7. 테스트 전략

- **골든 패스 리플레이**: pilot1 audit.md의 41개 엔트리를 스크립트화 — 동일한 답변 시퀀스를 API로 주입해 aiplc-state.md 스테이지 전이가 pilot1과 일치하는지 검증한다. 방법론 재현성의 회귀 테스트.
- **파서 유닛 테스트**: question-format-guide.md 예제 + pilot1의 실제 질문 파일들(pain-point-questions.md, strategy-questions.md, gtm-questions.md 등)을 픽스처로 사용.
- **E2E (Playwright)**: 위저드 전 구간 + Discovery→빌드→퍼블리시 1회 완주.

## 8. 구현 단계

1. **Phase 1 — Discovery 코어**: MicroVM 이미지(Claude Code+룰+하네스) + 파일 브리지 + 위저드/대시보드/문서 리뷰 + S3 동기화. 이것만으로 "비개발자가 Discovery 완주" 가치를 달성한다.
2. **Phase 2 — 프로토타입**: 캔버스 + iframe 프리뷰 + 이터레이션 루프.
3. **Phase 3 — 퍼블리시/핸드오프**: buildah→ECR 파이프라인 + traefik 라우팅 + 핸드오프 화면 + 컨테이너 하드닝.

## 스코프 제외 (YAGNI)

- Inception/Construction/Operations 단계 — 방법론 원형대로 개발자 워크스페이스 몫. Pathfinder의 경계는 Discovery Document + PROTOTYPE-*.md 핸드오프까지.
- 다국어 UI, SSO, 멀티테넌트 과금, 실시간 모순 감지, 룰 편집 UI.
