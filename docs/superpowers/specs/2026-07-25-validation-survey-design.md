# 검증 설문 — 문항 자동 생성 + 공개 응답 폼 + 대시보드 설계

날짜: 2026-07-25
상태: 설계 확정 (사용자 승인)

## 1. 배경과 목표

`prototype-validation.md` 룰의 Step 5는 PM에게 **검증 계획**(수집 방법·인원·기간)만 묻고,
Step 6은 PM이 **가져온** 피드백을 종합한다. 그 사이 — "최종 사용자에게 무엇을 물을지"와
"실제로 받아오기" — 는 룰에 없고 PM이 외부 도구(Google Forms 등)로 해야 한다.
이 기능이 그 갭을 메운다: **Pathfinder 안에서 문항을 생성하고, 공개 링크로 응답을 받아,
대시보드로 집계한다.**

룰 자체는 수정하지 않는다(룰은 방법론 데이터). 이 기능은 룰의 Step 5→6 사이를 잇는
**제품 기능**이며, CSV 내보내기가 Step 6의 `validation-results.md` 종합 입력이 된다.

사용자 결정 사항:

- **범위는 전 구간** — 문항 생성 + 공개 폼/링크 + 응답 저장 + 대시보드.
- **저장소는 S3** (SQLite 기각). SQLite는 응답 규모에는 충분하지만 이 인프라에서
  `userDataCausesReplacement: true` 때문에 코드 재배포 시 EC2·EBS가 교체되어 응답이
  전부 유실된다. S3는 `S3Store`·IAM·테스트 fake가 이미 갖춰져 있다.
- **문항 생성은 백엔드 에이전트 1턴** — 인프로세스 Strands 에이전트에 룰 기반
  프롬프트로 지시. MicroVM 부팅(4초+)이 불필요.
- **공개 폼 방어는 추측 불가 토큰 + 수동 마감** — 캡차·IP 리밋은 제외(아래 §5).
- **집계는 rollup 캐시 경유** — 개별 객체 병렬 get은 실측상 500건에서 2.6초.
  rollup 단일 객체는 395ms(6.7배, 요청 1회).

## 2. 성능 실측 (설계 근거)

실제 S3(서울 백엔드 → 도쿄 버킷, 12문항 응답 1건 ≈ 1.2KB) 계측:

| 응답 수 | list | 개별 병렬 get | rollup 단일 get |
|---|---|---|---|
| 200 | 120ms | 1.03s | — |
| 500 | 120ms | 2.61s | **395ms** |

- 커넥션 풀(10/32/64)은 병목이 아니다 — 세 값 모두 2.6초로 동일(크로스 리전 RTT 지배).
- **순차 get은 건당 61ms** — 집계 경로에서 절대 사용 금지.
- 500건 rollup 객체 크기 572KiB — 메모리·전송 모두 문제없음.

→ 쓰기는 객체-per-응답(동시 경합 없음), 읽기는 rollup 1회로 **경로를 분리**한다.

## 3. 데이터 모델

프로젝트 S3 프리픽스(`projects/{pid}/`) 하위:

```
prototypes/{slug}/survey/
├── questionnaire.json      # 문항 정의 + 토큰 + 상태
├── rollup.json             # 집계 캐시 — 대시보드가 읽는 단일 객체
├── responses/{uuid}.json   # 응답 1건 = 객체 1개 (정본)
└── archive/{closed_at}/    # 마감 후 재생성 시 이전 설문·응답·rollup 이관
```

**재생성 시 격리**: 마감된 설문을 새로 생성하면 이전 `questionnaire.json`·
`responses/`·`rollup.json`을 `archive/{closed_at}/` 로 옮긴 뒤 새 설문을 만든다.
같은 `responses/` 를 재사용하면 이전 문항에 대한 응답이 새 문항 집계·CSV에 섞여
수치가 조용히 오염된다 — 설문 1개는 자기 문항에 대한 응답만 본다.

프로젝트 프리픽스 **밖**(공개 토큰 역인덱스):

```
surveys/by-token/{token}.json   # {"project_id": ..., "slug": ...}
```

사람이 읽는 사본(기존 artifacts 뷰어가 `aiplc-docs/` 하위만 허용하므로 이 위치):

```
aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md
```

### questionnaire.json

```json
{"token": "<43자 URL-safe 랜덤>",
 "status": "open",
 "slug": "notam-ai-summary",
 "project_id": "notam3",
 "created_at": "2026-07-25T09:00:00Z",
 "closed_at": null,
 "title": "NOTAM AI 판독 지원 프로토타입 검증",
 "hypothesis": "PROTOTYPE md의 Validation Hypothesis 요약",
 "questions": [
   {"id": "q1", "text": "...", "type": "scale", "options": [], "required": true},
   {"id": "q2", "text": "...", "type": "choice",
    "options": ["매우 유용", "보통", "불필요"], "required": true},
   {"id": "q3", "text": "...", "type": "text", "options": [], "required": false}
 ]}
```

**문항 타입은 3종만**: `scale`(1-5 정수), `choice`(단일 선택), `text`(자유 응답).
다중 선택·분기 로직·파일 업로드는 스코프 제외(YAGNI).

### responses/{uuid}.json

```json
{"response_id": "<uuid4>", "submitted_at": "2026-07-25T10:11:12Z",
 "answers": {"q1": 4, "q2": "매우 유용", "q3": "요약 정확도가 인상적"}}
```

응답자 신원은 수집하지 않는다(익명) — 개인정보 취급 부담을 만들지 않는다.

### rollup.json (캐시, 정본 아님)

```json
{"count": 42, "rebuilt_at": "...",
 "per_question": {
   "q1": {"type": "scale", "n": 42, "mean": 4.1,
          "distribution": {"1": 0, "2": 2, "3": 8, "4": 18, "5": 14}},
   "q2": {"type": "choice", "n": 42,
          "counts": {"매우 유용": 25, "보통": 14, "불필요": 3}},
   "q3": {"type": "text", "n": 30, "samples": ["...", "..."]}
 }}
```

**정본/캐시 계약**: 응답 저장은 `responses/{uuid}.json` PUT 성공 시 완료다. 이어서 rollup
갱신을 시도하지만 **실패해도 응답은 성공(204)** 으로 답한다(로그만). 대시보드 조회 시
`rollup.count != len(list("responses/"))` 이면 개별 객체에서 재구축 후 덮어쓴다 —
캐시가 낡거나 깨져도 수치가 틀리지 않는다. `text` 응답은 rollup에 최대 20건만 샘플로
싣고, 전체 원문은 CSV 내보내기로 얻는다(rollup 무한 성장 방지).

## 4. 컴포넌트

| 모듈 | 역할 |
|---|---|
| `backend/pathfinder/survey/store.py` | `SurveyStore` — questionnaire 저장/조회, 토큰 역인덱스, 응답 append, rollup 갱신·재구축·조회, CSV 직렬화 |
| `backend/pathfinder/survey/builder.py` | `build_questionnaire(md, agent)` — PROTOTYPE md에서 가설·기능 추출 → 에이전트 1턴 → JSON 파싱·스키마 검증(실패 시 1회 재시도) |
| `backend/pathfinder/routes/surveys.py` | 관리 API(등록 프로젝트 확인 뒤) + 공개 API 2개(토큰) |
| `frontend/app/survey/[token]/page.tsx` | 공개 응답 폼 — Pathfinder 헤더·인증 없이 독립 렌더 |
| `frontend/components/prototypes/SurveyPanel.tsx` | 질문 생성 버튼·링크 복사·마감·대시보드 |
| `frontend/lib/api/surveys.ts` | 관리 API 클라이언트 + 공개 API 클라이언트(별 함수) |

`SurveyStore`는 `S3StoreLike`(get/put/list/delete_prefix)만 의존한다 — 기존
`tests/fakes/in_memory_s3.py`로 AWS 없이 전 계약 검증.

## 5. 라우트

### 관리 (기존 `/projects` 규약)

| 라우트 | 동작 |
|---|---|
| `POST /projects/{pid}/prototypes/{slug}/survey` | 문항 생성(에이전트 1턴) → questionnaire.json + 역인덱스 + md 사본 저장. 201 + `{token, url, questions}`. **열린 설문이 이미 있으면 409** — 문항 교체는 수집된 응답과 문항의 대응을 깨므로 허용하지 않는다. 마감된 설문이 있으면 새 설문으로 교체 생성하고(새 토큰), 이전 설문·응답은 `archive/{closed_at}/` 로 이관된다(§3) |
| `GET /projects/{pid}/prototypes/{slug}/survey` | questionnaire + rollup 집계. 없으면 404 |
| `POST /projects/{pid}/prototypes/{slug}/survey/close` | status=closed, closed_at 기록. 204. 멱등 |
| `GET /projects/{pid}/prototypes/{slug}/survey/responses.csv` | 원본 CSV(문항 헤더 + 응답 행) — 룰 Step 6 종합 입력 |

### 공개 (토큰만, 인증 없음)

| 라우트 | 동작 |
|---|---|
| `GET /survey/{token}` | 문항·제목만 반환. **`project_id`/`slug`/집계는 응답 본문에 절대 포함하지 않는다**. closed면 410 |
| `POST /survey/{token}` | 응답 저장. 204. closed면 410 |

공개 URL은 `/survey/{token}` — 프로젝트 ID·slug가 URL에도 본문에도 노출되지 않는다.
서버가 역인덱스로 내부 해석만 한다.

## 6. 데이터 흐름

### 문항 생성

1. PM이 프로토타입 탭에서 "질문 생성" 클릭
2. S3에서 `PROTOTYPE-{slug}.md` 로드(없으면 404) → Validation Hypothesis·Features 섹션 추출
3. 에이전트 1턴: "이 가설을 검증할 문항 6~10개를 만들라. 각 기능이 pain point를 해결했는지
   묻는 문항 포함. scale/choice/text 타입만. JSON으로만 응답" — 룰의 검증 관점(기능별
   시그널·pain point 매핑)을 프롬프트에 명시
4. JSON 스키마 검증 → 실패 시 1회 재시도 → 그래도 실패면 502
5. 토큰 생성(`secrets.token_urlsafe(32)`) → questionnaire.json + `surveys/by-token/{token}.json`
   + `validation-questionnaire.md` 저장
6. 201 + 공개 링크 반환 → UI가 링크 복사 버튼 노출

### 응답 수집

1. 응답자가 `/survey/{token}` 열기 → `GET /survey/{token}` → 문항 렌더
2. 제출 → `POST /survey/{token}` → 상한 검증(§7) → `responses/{uuid}.json` PUT
3. rollup 갱신 시도(실패 무시) → 204 → 폼이 완료 메시지 표시(중복 제출 방지: 완료 화면 고정)

### 대시보드

1. `GET .../survey` → rollup get 1회 + `list("responses/")` 1회로 카운트 검증
2. 불일치면 개별 객체 병렬 get으로 재구축 후 rollup 덮어쓰기
3. 응답 수·문항별 집계(scale은 평균+분포 바, choice는 카운트, text는 샘플) 렌더
4. "CSV 내보내기" 버튼 → 룰 Step 6로 넘길 원본 확보

## 7. 에러 처리·보안

| 상황 | 처리 |
|---|---|
| 없는/잘못된 토큰 | 404 (설문 존재 여부를 구분해 알려주지 않음) |
| 마감된 설문 조회·응답 | 410 Gone + 안내 문구 |
| rollup 갱신 실패 | 응답은 204 성공, 로그만 → 다음 대시보드 조회에서 재구축 |
| rollup 카운트 불일치 | 개별 객체에서 재구축 후 덮어쓰기 |
| 문항 생성 실패(에이전트/파싱) | 502 + sanitize된 사유(자격증명 노출 금지 — 기존 패턴) |
| PROTOTYPE md 없음 | 404 |
| 응답 본문 과대 | 문항당 2000자·전체 32KB 초과 → 413 |
| 알 수 없는 문항 id·타입 불일치 | 400 (스키마 밖 키는 거부) |
| 응답 수 상한 | 설문당 1000건 초과 시 429 + 마감 안내(S3 비용·rollup 크기 폭주 방지) |

**의도적으로 제외한 방어**: 캡차, 이메일 인증, IP 리밋. IP 리밋은 백엔드 재시작 시
초기화되는 인메모리 방어라 실효가 낮고, 워크숍 규모에서 오탐(같은 NAT 뒤 참석자
다수)이 더 해롭다. 방어선은 **토큰 비공개 + 수동 마감 + 응답 수 상한**이다.
이는 워크숍 데모 규모의 명시적 트레이드오프다.

**공개 경로가 만드는 새 노출면**: 인증 없는 쓰기 경로가 처음 생긴다. 따라서
(a) 공개 응답에 내부 식별자를 넣지 않고, (b) 공개 라우트는 questionnaire·응답 저장
외 어떤 프로젝트 데이터에도 접근하지 않으며, (c) 응답 본문은 문항 스키마에
정의된 키만 수용한다(임의 키 저장 금지 — S3 오염 방지).

## 8. 테스트

- **백엔드 단위**: fake S3로 `SurveyStore`(append, rollup 갱신, 카운트 불일치 감지 후
  재구축, 토큰 역인덱스, CSV 직렬화, text 샘플 상한); `build_questionnaire`는 fake
  에이전트로 JSON 파싱·스키마 위반 재시도·재시도 후 실패
- **재생성 격리**: 마감 후 재생성 시 이전 응답이 `archive/` 로 이관되고 새 설문의
  집계·CSV에 **이전 응답이 섞이지 않음**을 단정(조용한 수치 오염 방지)
- **백엔드 라우트**: 생성 201/404/409, 조회 404, 마감 204·멱등, 공개 GET/POST 200/204,
  마감 후 410, 없는 토큰 404, 413 상한, 400 스키마 밖 키, 429 응답 수 상한,
  **공개 응답 본문에 `project_id`/`slug` 부재 단정**
- **성능 회귀 가드**: 대시보드 조회가 응답 수와 무관하게 rollup get 1회 + list 1회임을
  S3 호출 카운트로 단정(개별 get으로 회귀하면 실패)
- **프론트 단위**: 공개 폼 3타입 렌더·필수 검증·제출·완료 화면·410 처리; 대시보드
  집계 렌더(평균·분포·카운트·샘플); 링크 복사·마감 버튼 상태
- **e2e**: 실 S3·실 Bedrock 필요 — Playwright 제외, 수동 체크리스트에 항목 추가
  (기존 방침 동일)

## 9. 스코프 제외

- 룰 파일(`prototype-validation.md`) 수정 — 룰은 방법론 데이터, 이 기능은 제품 기능
- 다중 선택·분기 로직·파일 업로드 문항
- 응답자 신원 수집·중복 제출 서버측 차단(익명 수집, 완료 화면 고정으로만 억제)
- 문항 수동 편집 UI — 생성된 `validation-questionnaire.md`를 문서 패널에서 읽는 것까지.
  문항을 바꾸려면 마감 후 재생성
- 응답 실시간 푸시(SSE) — 대시보드는 수동 새로고침
- 캡차·이메일 인증·IP 리밋(§7 근거)
- 룰 Step 6의 종합 자동화 — CSV를 PM이 기존 Discovery 흐름에 넣는다
