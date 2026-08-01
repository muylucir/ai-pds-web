# 프로젝트별 AI 모델 선택 + 관리자 모델 카탈로그

날짜: 2026-08-01

## 문제

`ANTHROPIC_MODEL`은 프로세스 전역 환경변수 하나다. 모델을 바꾸려면 EC2에
들어가 env를 고치고 백엔드를 재시작해야 하고, 그 순간 **모든 프로젝트가 함께**
바뀐다. 워크숍에서 참가자별로 다른 모델을 쓰거나, 한 프로젝트만 상위 모델로
올려 비교하는 것이 불가능하다.

두 번째 문제는 모델 목록이 코드 상수라는 것이다. `infra/lib/backend-permissions.ts`의
`INVOKABLE_MODELS`가 invoke를 허용하는 5개를 못 박고 있어, 새 Claude 모델이
나오면 그 파일을 고치고 `cdk deploy`를 돌려야 한다. 워크숍 운영자가 할 수 있는
일이 아니다.

## 설계

### 1. 데이터 — 모델 카탈로그는 S3에 산다

프로젝트 매니페스트와 같은 규율이다. 관리자가 편집하는 값이므로 코드 상수가
아니고, EC2 교체를 넘겨야 하므로 인메모리가 아니다.

버킷 루트의 **`models/catalog.json`** — `projects/`·`sessions/`·`surveys/` 옆의
네 번째 프리픽스다. 프로젝트 프리픽스 밖에 두는 이유는 카탈로그가 프로젝트보다
먼저 존재해야 하기 때문이다(프로젝트 생성 화면이 프로젝트가 없는 상태에서
이것을 읽는다). `surveys/by-token/`이 프로젝트 프리픽스 밖에 있는 것과 같은
이유다.

```json
{
  "models": [
    {"name": "Opus 5",     "model_id": "global.anthropic.claude-opus-5",      "display": true},
    {"name": "Opus 4.6",   "model_id": "global.anthropic.claude-opus-4-6-v1", "display": true},
    {"name": "Sonnet 5",   "model_id": "global.anthropic.claude-sonnet-5",    "display": true},
    {"name": "Sonnet 4.6", "model_id": "global.anthropic.claude-sonnet-4-6",  "display": true}
  ]
}
```

규칙:

- **`model_id`가 키다.** 같은 id를 두 번 등록하면 409. `name`은 표시용이라
  중복을 허용한다(같은 모델을 다른 이름으로 부를 이유는 없지만, 이름 충돌로
  등록을 막을 이유도 없다).
- **파일이 없으면 위 4개를 코드의 시드 목록으로 반환한다.** 배포 직후 관리자가
  아무것도 하지 않아도 콤보박스가 채워져야 한다. 반대로 "빈 카탈로그"를 유효
  상태로 두면 첫 프로젝트 생성이 막힌다 — 시드는 편의가 아니라 부트스트랩
  경로다. 이 시드는 **읽기 시점의 폴백일 뿐이고 파일로 쓰지 않는다**: 관리자가
  모델을 하나 추가하면 그때 4+1개가 파일로 쓰이고, 이후로는 파일이 진실이다.
- **`display: true`가 5개면 여섯 번째를 켜려는 요청은 400.** 등록 자체는
  무제한. 상한을 등록이 아니라 표시에 두는 것이 요구사항이다("관리자 페이지에선
  여러 모델을 등록 삭제할 수 있지만 디스플레이는 5개만").
- 표시 5개를 고르는 기준은 **관리자가 켜고 끄는 `display` 플래그**다. 정렬 상위
  5개가 아니다 — 그러면 6번째로 밀린 모델이 화면에서 조용히 사라지고, 새 모델을
  보이게 하려면 무엇이 사라졌는지 관리자가 알 수 없다. 플래그는 여섯 번째를
  켜려는 순간 400으로 "무엇을 먼저 내리라"고 말한다.
- **버킷 미설정(로컬/테스트)이면 시드 목록을 그대로 쓰고 쓰기는 거부한다.**
  `durable_projects_enabled()`와 같은 규율 — 필수 env가 없으면 그 기능을
  생략하되, 읽기는 되게 해서 로컬 개발이 아무 설정 없이 돈다.

### 2. 프로젝트의 선택값은 매니페스트에 복사한다

`project.json`에 `model_id` 필드를 추가한다:

```json
{"project_id": "pilot2", "name": "...", "created_at": "...",
 "model_id": "global.anthropic.claude-opus-5"}
```

**카탈로그를 참조(FK)하지 않고 값을 복사한다.** 관리자가 모델을 카탈로그에서
지워도 진행 중인 프로젝트가 모델을 잃으면 안 된다. 카탈로그는 "무엇을 새로
고를 수 있는가"의 목록이고, 프로젝트의 `model_id`는 "이 프로젝트가 무엇으로
도는가"의 사실이다 — 둘의 수명이 다르다.

구 매니페스트에는 이 필드가 없다. `None`을 허용하고 폴백으로 떨어진다(§3).

`restore_projects()`의 반환은 3-tuple에서 4-tuple이 된다
(`(pid, name, created_at)` → `(pid, name, created_at, model_id)`).
`ProjectRegistry`에 `_model_id` 맵과 `get_model_id(pid)`를 추가하고
`register(..., model_id=None)`으로 받는다.

### 3. 주입 — 세 지점 전부

`ANTHROPIC_MODEL`을 읽는 곳이 세 곳이고 셋 다 프로세스 전역 env를 읽는다:

| 위치 | 무엇 | pid를 이미 아는가 |
|---|---|---|
| `app.py:224` `driver_factory` | Discovery 에이전트 | 예 |
| `app.py:293` `builder_factory` | 프로토타입 빌드 에이전트 | 예 (`proto_session_factory`의 클로저) |
| `app.py:330` `questionnaire_agent_factory` | 설문 문항 생성 | **아니오 — 인자가 없다** |

`app.py`에 해석 함수 하나를 둔다:

```python
def project_model(project_id: str) -> str | None:
    """프로젝트의 모델. 미지정이면 env 기본값(= 배포의 opus-4-8)으로 떨어진다.

    반환이 None일 수 있다: 로컬 개발에서 ANTHROPIC_MODEL도 없는 경우다.
    ClaudeDriver/PrototypeBuilder는 None을 받으면 env를 넣지 않아 SDK 기본값으로
    가고, 이것이 종전 동작이다.
    """
    return registry.get_model_id(project_id) or os.environ.get("ANTHROPIC_MODEL")
```

폴백 순서가 **프로젝트 → env → None**인 이유: env 기본값은 이 기능 이전에 만든
프로젝트(매니페스트에 `model_id`가 없는 것)가 계속 돌기 위한 것이고, `None`은
로컬 개발이 설정 없이 돌기 위한 것이다.

`questionnaire_agent_factory`는 **`project_id` 인자를 추가한다.** 호출부는
`routes/surveys.py:66` 한 곳, 테스트는 `test_routes_surveys.py`의 2곳이다.
여기가 유일하게 `os.environ["ANTHROPIC_MODEL"]`을 **KeyError로 요구**하는
지점이라, 이것을 고치지 않으면 카탈로그로 모델을 골라도 설문 생성만 전역 env를
쓴다. `project_model()`이 `None`을 반환할 수 있으므로 이 자리에서는 명시적으로
처리한다 — `BedrockModel(model_id=None)`은 의미가 없으니, `None`이면 종전처럼
KeyError를 내는 대신 502로 번역될 `RuntimeError`를 던지고 로그에 이유를 남긴다
(설문 생성 실패는 이미 `surveys.py`가 502로 감싼다).

`ClaudeDriver`와 `PrototypeBuilder`는 이미 `anthropic_model` 생성자 인자를 갖고
있어 손댈 필요가 없다. `StrandsDriver`(폴백 드라이버)는 `os.environ`을 직접
읽는데, 폴백 경로이고 워크숍 후 삭제 예정이므로 **이번 범위에서 제외한다** —
`PATHFINDER_DISCOVERY_DRIVER=strands`로 돌리면 프로젝트별 모델이 무시되고 env
기본값을 쓴다. 이 제약을 `driver.py`에 주석으로 남긴다.

### 4. IAM — 와일드카드로 넓힌다

```typescript
resources: [
  `arn:aws:bedrock:*:${account}:inference-profile/global.anthropic.claude-*`,
  `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`,
]
```

`INVOKABLE_MODELS` 상수와 `infra/test/hosting-stack.assert.ts`의 5개 단정을
함께 교체한다.

**이것이 "관리자 화면에서 새 모델 추가"가 실제로 동작하는 유일한 조건이다.**
명시 목록을 유지하면 카탈로그에 등록해도 IAM이 막아 첫 대화 턴에
`AccessDenied`가 나고, 그 실패는 백엔드 로그에만 남는다(README 트러블슈팅의
바로 그 증상). 관리자에게 "모델을 추가할 수 있다"고 보여주면서 실제로는 CDK
배포가 필요한 상태가 최악이다.

허용 범위가 "모든 global Anthropic Claude 추론 프로파일"로 넓어지는 것은
의도된 교환이다. 이 롤은 Bedrock invoke 외에 하는 일이 없고(S3는 별도
statement), 어떤 Claude 모델을 부르든 데이터 경계는 같다.

`MODEL = 'global.anthropic.claude-opus-4-8'`은 **그대로 둔다.** 카탈로그의 4개에
없지만 두 역할이 남는다: 이 기능 이전에 만든 프로젝트, 그리고 모델 미지정 시의
폴백. 와일드카드가 이 값을 포함하므로 IAM은 문제없다 —
`hosting-stack.assert.ts`에 그것을 단정하는 테스트를 넣는다.

### 5. API

**관리자 (`/admin/models`)** — `admin_users.py`와 같은 방식으로 라우터 전체에
`dependencies=[Depends(require_admin)]`을 붙인다. 라우트마다 붙이는 것을 잊을
여지를 없앤다.

| 메서드 | 경로 | 동작 |
|---|---|---|
| `GET` | `/admin/models` | 전체 등록 목록 (`display` 포함) |
| `POST` | `/admin/models` | `{name, model_id, display}` 추가. 중복 409, 표시 6개째 400 |
| `PATCH` | `/admin/models/{model_id}` | `name`·`display` 수정 |
| `DELETE` | `/admin/models/{model_id}` | 제거 |

**일반 (`require_user`)**

- `GET /models` → `display: true`인 것의 **`{name, model_id}`만**, 최대 5개.
  콤보박스가 부르는 곳이다. `display: false`인 항목은 아예 보내지 않는다 —
  일반 사용자에게 고를 수 없는 것을 보낼 이유가 없고, 프론트가 필터링을
  잊는 경로를 없앤다.
- `POST /projects` → `model_id` 필드 추가(optional).
- `GET /projects` → 각 항목에 `model_id` 추가.
- **`GET /projects/{pid}` 신설** → `{project_id, name, created_at, model_id}`.
  지금 이 라우트가 없다(목록만 있다). 배지를 그리려면 워크스페이스·프로토타입
  화면이 자기 프로젝트 하나의 메타데이터를 읽을 수 있어야 하고, 그것을 목록
  API로 하면 페이지네이션을 뒤져야 한다. `ensure_workspace`를 타지 않고
  레지스트리만 읽는다 — 배지 하나가 워크스페이스 lazy 초기화를 유발하면 안
  된다(`list_projects`의 `_progress`가 같은 이유로 S3를 직접 읽는다).
  미등록 pid는 404.

**주어진 `model_id`가 카탈로그의 표시 목록에 없으면 400.** 임의 문자열이 들어와
첫 대화 턴에 `AccessDenied`(와일드카드 밖) 또는 `ValidationException`(존재하지
않는 프로파일)으로 터지는 것을 생성 시점에 막는다. 검증 기준을 "등록 목록"이
아니라 "표시 목록"으로 두는 이유: 표시가 꺼진 모델은 관리자가 **의도적으로
내린 것**이므로 새 프로젝트가 그것을 고르면 안 된다.

`model_id`는 URL 경로 세그먼트에 들어가는데 `.`과 `-`, 영숫자, `:`만 포함하므로
인코딩 문제가 없다(`adminUsers.ts`의 `@` 처리 같은 것이 필요 없다).

### 6. 프론트엔드

**프로젝트 생성 폼** (`CreateProjectForm.tsx`) — ID·이름 옆에 세 번째 필드로
`<select>`. 마운트 시 `GET /models`로 채우고 **이름만** 표시한다(요구사항:
"콤보박스에는 모델 이름만 표시"). 첫 항목을 기본 선택.

조회 실패 시엔 셀렉트를 비활성화하고 **모델 없이 생성 가능하게 둔다** —
백엔드가 env 기본값으로 떨어지므로 종전과 같이 동작한다. 카탈로그 조회 실패가
프로젝트 생성 전체를 막는 것은 과하다.

**관리자 페이지** (`/admin/models`) — `/admin/users`와 같은 모양: 테이블 +
추가 모달. 각 행에 표시 토글, 이름 수정, 삭제. `UserMenu`의 admin 링크 옆에
항목을 추가한다. `gate.ts`는 `/admin` 프리픽스 전체를 admin으로 게이팅하므로
수정이 필요 없다.

**현재 모델 배지** — 워크스페이스와 프로토타입 화면에 프로젝트의 모델 이름을
표시한다. 프로젝트마다 모델이 다르면 지금 무엇으로 도는지 화면에 없으면 알 수
없다. `GET /projects/{pid}`로 `model_id`를 받고 `GET /models`의 이름과 대조해
표시한다.

대조 실패(카탈로그에서 지워진 모델, 또는 `model_id`가 `null`인 구 프로젝트)의
처리:

- 카탈로그에 없는 `model_id` → **원문을 그대로 보여준다.** 값을 복사해 두는
  §2 설계의 결과가 화면에서도 정직하게 드러나야 한다.
- `model_id`가 `null` → 배지를 그리지 않는다. "env 기본값으로 돈다"는 것을
  프론트가 알 방법이 없고(그 값은 서버 프로세스의 env다), 추측한 이름을
  보여주는 것보다 아무것도 안 보여주는 게 낫다.

배지가 헤더의 "Bedrock 연결됨" 배지 옆에 들어가므로 `AppHeader`가 `model_id`를
optional prop으로 받는다 — 프로젝트가 없는 화면(프로젝트 목록)에서는 없다.

### 7. 테스트

**백엔드**
- 카탈로그 store: 파일 없음 → 시드 4개, 시드는 파일로 쓰이지 않음, 중복
  `model_id` 409, 표시 6번째 400, 버킷 미설정 시 읽기 성공·쓰기 거부
- `GET /models`가 `display: true`만 반환하고 `{name, model_id}`로 축약되는지
- `GET /projects/{pid}`가 워크스페이스를 만들지 않는지(미등록 404, 등록만 된
  프로젝트도 200) — `ensure_workspace`를 타면 배지 하나가 lazy 부팅을 유발한다
- `POST /projects`: 표시 목록의 `model_id` 허용, 표시 꺼진 것 400, 미등록
  문자열 400, 미지정 허용
- `project_model()` 폴백 순서 3단계
- `restore_projects()` 4-tuple + 구 매니페스트(`model_id` 없음) 호환
- `questionnaire_agent_factory(pid)` 시그니처 변경과 모델 없음의 RuntimeError
- `test_auth_route_coverage`가 `/admin/models*`를 admin 라우트로 인식

**인프라**
- 와일드카드 ARN 단정으로 교체
- `MODEL` 기본값이 그 와일드카드 패턴에 포함되는지 (폴백이 실제로 invoke
  가능한지 — 이것이 없으면 §4의 "그대로 둔다"가 검증되지 않는다)

**프론트엔드**
- 셀렉트가 이름만 렌더하고 `model_id`를 노출하지 않는지
- 5개 상한
- `GET /models` 실패 시에도 생성 버튼이 살아 있는지
- 관리자 페이지 CRUD + 표시 토글의 6번째 400 처리
- 배지가 카탈로그에 없는 `model_id`를 원문으로 표시하는지

## 범위 밖

- **`StrandsDriver`의 프로젝트별 모델** — 폴백 경로, 워크숍 후 삭제 예정(§3)
- **프로젝트 생성 후 모델 변경** — 진행 중인 대화의 모델을 바꾸면 트랜스크립트
  중간에 모델이 갈리고, 프롬프트 캐시가 무효화된다. 생성 시점 1회 결정으로
  둔다(사용자 요구사항: "프로젝트 생성 화면에서 ... 프로젝트 전체에 주입")
- **모델별 파라미터 커스터마이즈**(effort, max_tokens) — 지금 두 드라이버가
  각자 고정값을 쓰고, 그것을 프로젝트별로 여는 것은 별개 스펙
