# Final Review Fixes — Report

네 건의 최종 리뷰 finding을 모두 고쳤다. 아래는 finding별 변경 내용, 네 가지
증명, 테스트 결과, 우려 사항이다.

## Finding 1 (CRITICAL) — nginx `/api/*` → FastAPI 오배선

### 추적 결과 (수정 전에 먼저 확인한 것)

브라우저가 부르는 백엔드 호출은 전부 `${API_BASE_URL}`(배포 시 `/api`) 경유이거나
Next 서버 컴포넌트 경유다 — `frontend/lib/api/client.ts`, `prototypes.ts`,
`surveys.ts`, `sse.ts`, `http.ts`를 모두 확인했다. `/proto/{pid}/{slug}` 공개
프리뷰 프록시(`backend/pathfinder/routes/proto_public.py`)도 마찬가지다 —
`prototypePreviewUrl()`(`frontend/lib/api/prototypes.ts:133-135`)이 만드는 URL이
이미 `${API_BASE_URL}/proto/...`, 즉 배포에서는 `/api/proto/...`다. **결론: nginx가
FastAPI(:8000)를 직접 가리킬 이유가 이제 하나도 없다.** `/api/`·`/` 두 location을
모두 `:3000`(Next)으로 합쳤다.

라우팅을 바꾸며 잡은 정확성 함정: 기존 `/api/` 블록은 `proxy_pass
http://127.0.0.1:8000/;`(끝에 슬래시)로 `/api/` 접두어를 **벗겨서** FastAPI에
넘겼다. `:3000`으로 바꾸면서 접두어를 벗기면 안 된다 — Next의 route handler는
`/api/[...path]`에 마운트되어 있어 리터럴 `/api/...` URI가 필요하다. 그래서
새 지시자는 `proxy_pass http://127.0.0.1:3000;`(슬래시 없음, URI 그대로 전달)다.

### 변경 — `infra/lib/user-data.ts`

- `/api/` location을 제거하고 `/` location 하나로 합쳤다. `proxy_pass`를
  `http://127.0.0.1:3000`(URI 보존)으로 통일.
- SSE 지시자(`proxy_buffering off`, `proxy_read_timeout 3600s`)를 그 하나뿐인
  location으로 옮겼다 — 이제 이 요청 경로는 browser → nginx → Next(route
  handler) → FastAPI이므로, SSE가 실제로 지나가는 지점에 지시자가 있어야 한다.
- `PATHFINDER_BACKEND_URL`은 systemd 유닛에도, `user-data.ts` 전체에도 설정되어
  있지 않음을 grep으로 확인했다 — Next의 route handler(`app/api/[...path]/
  route.ts:29`)가 `process.env.PATHFINDER_BACKEND_URL ?? "http://localhost:8000"`
  로 폴백하므로 EC2 박스 안에서는 별도 조치 없이 올바르게 동작한다.

### 부수 발견 — `frontend/app/api/[...path]/route.ts`

`GET`/`POST`/`PUT`/`DELETE`만 export하고 있었는데, `proto_public.py`의 두
라우트는 `methods=["GET","POST","PUT","DELETE","PATCH","HEAD","OPTIONS"]`로
7개 전부를 받는다(호스팅된 프로토타입 자체 서버로 임의 메서드를 전달하는
프록시라서). Finding 1 이전에는 nginx가 `/api/`를 FastAPI로 직접 보냈으므로
이 메서드들이 문제없이 도달했지만, 지금은 전부 Next를 거치므로 export가
없으면 Next가 자체적으로 405(또는 맨 OPTIONS면 자동 생성 204)로 응답해버려
조용히 기능이 좁아진다.

리뷰에서 지적받아 반영: 처음엔 `PATCH`·`OPTIONS`만 명시적으로 export하고
`HEAD`는 Next가 `GET` 핸들러로 암묵적으로 자동 구현하는 데 맡겼는데, 다음
사람이 나머지 메서드를 볼 때 7개 중 몇 개가 실제 export인지 한눈에 안
보이는 문제가 있었다. `HEAD`도 명시적으로 export해 7개 전부가 named export로
드러나게 했다. `HEAD`가 요청 본문을 붙이지 않는다는 보장은 `proxy()`의
`req.method !== "GET" && req.method !== "HEAD"` 가드가 (어떤 export로
호출됐는지가 아니라) 런타임 `req.method`를 직접 보고 처리하므로 세 export
전부에 그대로 적용됨을 확인했다. `route.test.ts`에 7개 메서드 전부가
export되어 있고 실제로 같은 메서드로 백엔드에 전달되는지 확인하는 회귀
테스트(`it.each`)를 추가했다 — 다음 사람이 조용히 좁히면 즉시 실패한다.

같은 파일의 헤더 주석 — "이 `/api` 프록시는 dev/데모 편의이며, 프로덕션은 …
대체한다" — 이 finding 1이 지목한 대로 정반대였다. Next의 이 라우트가 바로
쿠키→Bearer 번역이 일어나는 지점이자 프로덕션 인증 경로 자체라는 내용으로
다시 썼다.

### nginx location 구조 선택

`/api/`와 `/`를 별도 location으로 유지하고 둘 다 `:3000`을 가리키는 안도
검토했지만, 두 location이 완전히 같은 upstream·같은 지시자를 향한다면 굳이
나눌 이유가 없고 나뉘어 있으면 두 블록이 시간이 지나며 어긋날 위험만
생긴다. 그래서 `location / {}` 하나로 합쳤다 — SSE 지시자(`proxy_buffering
off`, `proxy_read_timeout 3600s`)가 있어야 할 위치를 "여기 하나뿐"으로
고정하는 효과도 있다.

### 테스트 — `infra/test/user-data.assert.ts`

- `proxy_pass http://127.0.0.1:8000`가 어디에도 없음을 단정.
- nginx conf 블록을 파싱해 `location`이 정확히 하나이고 그게 `location / {`
  임을, `:3000`으로 가는지, SSE 지시자 둘 다 그 location에 있는지 확인.
- `/api/auth/`가 프론트로 간다는 것을 "별도의 `/api/` location이 존재하지
  않는다"는 사실로 직접 확인(nginx는 최長 접두어 매칭이므로 `/api/` 전용
  block이 없는 한 `/api/auth/`도 유일한 `location /`에 매칭된다).

## Finding 2 (IMPORTANT) — 시크릿이 부트스트랩 로그에 평문으로 남음

### 변경 — `infra/lib/user-data.ts`

- 부트스트랩 시작부에 `touch` + `chmod 600 /var/log/pathfinder-bootstrap.log`를
  `exec > >(tee -a ...)` **이전**에 추가 — 로그 파일이 생성되는 순간부터 root
  전용이다.
- Cognito 클라이언트 시크릿 조회(`COGNITO_SECRET=$(aws cognito-idp
  describe-user-pool-client ...)`)를 `set +x` / `set -x`로 감쌌다.
- X-Origin-Verify 헤더 시크릿 조회(`SECRET=$(aws secretsmanager
  get-secret-value ...)`)도 같은 이유로 `set +x` / `set -x`로 감쌌다(리뷰가
  지목한 "인접한 노출"도 함께 고침).

### 테스트 — `infra/test/user-data.assert.ts`

- `chmod 600 ...bootstrap.log`가 렌더링되는지 단정.
- 두 시크릿 대입 각각에 대해, 그 대입 바로 앞의 마지막 `set +x`가 마지막
  `set -x`보다 뒤에 있는지(즉 트레이스가 꺼진 채로 대입이 실행되는지), 그리고
  대입 뒤 4줄 이내에 `set -x`가 있는지(다시 켜지는지)를 직접 검증한다 — 단순히
  "어딘가에 set +x가 있다"만 보면 순서가 어긋난 회귀를 못 잡는다.

## Finding 3 (IMPORTANT) — `/docs`·`/openapi.json`·`/redoc` 익명 노출

### 변경 — `backend/pathfinder/app.py`

`_docs_openapi_url()` 순수 함수를 추가해 `cognito_config()`가 설정돼 있으면
`None`(→ `/docs`·`/redoc`도 함께 꺼짐, FastAPI가 둘 다 `openapi_url`을 전제로
등록하기 때문)을, 아니면 `"/openapi.json"`을 반환한다. `FastAPI(...)` 생성자에
`openapi_url=_docs_openapi_url()`로 연결했다.

`cognito_config()`를 임포트 시점에 호출하는 것이 안전한 이유: 이 함수는 매
요청 호출(`require_user`)과 동치인 순수 env 읽기이고, 반쯤 설정된 상태면
즉시 `RuntimeError`로 죄는 편이 첫 요청까지 기다리는 것보다 낫다(기존
`jwks_cache()`도 이미 이 함수를 임포트 후 지연 호출한다).

별도 함수로 뽑은 이유(구현 중 발견한 함정): 처음에는 테스트에서
`importlib.reload(pathfinder.app)`으로 env 변경 후 앱을 재생성하려 했으나,
`app.py` 모듈 전역 싱글턴(`registry` 등)이 reload로 새로 만들어지면서 `from
pathfinder.app import registry`로 그 객체를 직접 참조 중인 다른 테스트
파일들(`test_routes_answers.py`, `test_routes_artifacts.py`,
`test_routes_discovery.py`)이 대량 `KeyError`로 깨졌다(실측). `_docs_
openapi_url()`을 순수 함수로 분리해 그것만 monkeypatch로 검증하고, 실제
404/200 확인은 module 전체가 아니라 같은 방식으로 만든 독립 `FastAPI` probe
앱으로 하는 방식으로 우회했다.

### 테스트 — `backend/tests/test_auth_route_coverage.py`

- `test_docs_openapi_url_is_none_when_auth_is_configured` /
  `test_docs_openapi_url_is_set_when_auth_is_not_configured`: 함수 자체의
  결정을 monkeypatch(env)로 검증.
- `test_docs_are_absent_on_the_real_app_when_openapi_url_is_none` /
  `test_docs_exist_on_the_real_app_when_openapi_url_is_set`: 독립 `FastAPI`
  probe 앱으로 `/openapi.json`·`/docs`·`/redoc`이 실제로 404/200을 반환하는지
  end-to-end 확인.
- `_app_routes()`의 docstring에 "빌트인 문서 라우트 제외"가 더 이상 우연한
  구멍이 아니라 기록된 결정임을 남겼다.

## Finding 4 (IMPORTANT) — `credentials: "include"` vs `allow_credentials=False`

### 확인한 것

`PATHFINDER_CORS_ORIGINS`를 설정하는 곳은 `backend/.env.example`(단일 origin
`http://localhost:3000`)과 README 기본값뿐이고, 둘 다 `*`가 아니다. 인프라
어디에도 이 값을 설정하는 코드가 없다(`infra/lib/*.ts`에 없음 — CDK 스택은
이 env를 건드리지 않는다). `*`가 될 수 있는 경로가 현재 없으므로
`allow_credentials=True`는 안전하다.

### 변경 — `backend/pathfinder/app.py`

`allow_credentials=False` → `True`. 위 블록 주석의 "쿠키를 안 쓴다"는 이제
거짓인 문장을 지우고, `frontend/lib/auth.ts`의 `CREDENTIALS = "include"`와
`/api` 프록시가 쿠키를 Bearer로 번역하는 지점을 근거로 왜 필요한지, 왜
안전한지(명시적 allowlist)를 다시 썼다.

### 테스트 — `backend/tests/test_cors.py`

`test_preflight_allows_credentials` / `test_simple_request_allows_credentials`
추가 — preflight·simple 두 응답 모두 `Access-Control-Allow-Credentials: true`
확인.

## 부수 — 헤더 주석 정정

`frontend/app/api/[...path]/route.ts`의 "이건 dev/데모 편의, 프로덕션은
대체한다" 주석이 finding 1 이후로는 정반대다. 프로덕션 인증 경로 자체라는
내용으로 교체(위 Finding 1 절 참고).

## 네 가지 증명

**1) nginx 렌더 결과 + 추적한 경로**
```
location / {
  proxy_pass http://127.0.0.1:3000;
  ...
  proxy_buffering off;          # SSE 즉시 전달 (browser -> nginx -> Next -> FastAPI)
  proxy_read_timeout 3600s;
}
```
`/api/` 전용 location 없음. 추적해서 확인한, 이전에 `/api/` → FastAPI에
의존하던 모든 경로: `client.ts`/`http.ts`/`prototypes.ts`/`surveys.ts`/
`sse.ts`의 모든 fetch 호출, `/proto/{pid}/{slug}` 프리뷰 프록시, `/survey/
{token}` 공개 설문 — 전부 `${API_BASE_URL}`(`/api`) 하나를 거치므로 `/api/`
가 Next로 가면 자동으로 전부 이어진다. FastAPI를 nginx가 직접 가리켜야 할
경로는 하나도 없었다.

**2) 시크릿 xtrace 억제 + chmod**
```
touch /var/log/pathfinder-bootstrap.log
chmod 600 /var/log/pathfinder-bootstrap.log
...
set +x
COGNITO_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id ... --client-id ... \
  --query 'UserPoolClient.ClientSecret' --output text --region ...)
set -x
...
set +x
SECRET=$(aws secretsmanager get-secret-value --secret-id ... --query SecretString --output text --region ...)
set -x
```

**3) `/openapi.json` 상태**
```
WITH auth configured:  /openapi.json -> 404
WITHOUT auth (local):  /openapi.json -> 200
```

**4) `Access-Control-Allow-Credentials`**
```
preflight status: 200
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Credentials: true

simple GET status: 200
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Credentials: true
```

## 테스트 결과

- `cd infra && npx tsc --noEmit && npm test` — clean, 5개 assert 파일 전부 OK.
- `cd backend && .venv/bin/python -m pytest -q` — **536 passed**.
- `cd frontend && npx vitest run && npx tsc --noEmit` — **523 passed**(원래
  516 + 7개 메서드 회귀 테스트), tsc clean.

## 우려 사항

없음. 네 finding 모두 리뷰가 제시한 방향으로 고쳤고, 위 네 증명으로 직접
확인했다. 다만 두 가지는 판단이 들어간 지점이라 기록해 둔다:

- Finding 1의 "FastAPI가 nginx를 통해 더 직접 노출될 필요가 있는가"에 대한
  답은 **아니오**다 — 위 추적 결과대로 브라우저가 부르는 모든 경로가
  `/api` 하나를 거친다. 다른 결론을 원한다면 알려달라.
- Finding 3에서 `cognito_config()`를 임포트 시점에 호출하는 것이 안전하다고
  판단했다 — 매 요청 호출과 동일한 순수 env 읽기이고, 반쯤 설정된 상태는
  기존에도 fail-closed로 즉시 예외를 던지도록 설계돼 있어(브리프의 기존
  규율) 이 시점에 앞당겨 터뜨리는 것이 오히려 더 일찍 사고를 드러낸다.
