# 모든 대화는 한국어로 진행
# 툴 호출 파라미터(JSON)의 한글 등 비ASCII 문자열은 항상 리터럴 UTF-8로 작성하고, \uXXXX 유니코드 이스케이프로 표기하지 않는다.
# 프로토타입을 생성할때 디자인은 shadcn-design 스킬을 사용

## 프로세스·포트 — Pathfinder 자신을 죽이지 않는다 (최우선)

**너는 Pathfinder를 실행하고 있는 바로 그 서버 안에서 돌고 있다.** 백엔드와
프론트엔드가 너와 **같은 유저(`pathfinder`)로** 돌기 때문에, 네가 보내는 신호는
아무 방어 없이 그 프로세스들에 닿는다. 실제로 사고가 났다(2026-08-01): 빌드
에이전트가 브라우저 검증을 위해 Playwright chromium을 띄웠고, 그 과정에서 포트
3000의 프로세스가 SIGKILL로 죽었다 — 그것은 Pathfinder 프론트엔드였고, 워크숍
참가자 화면에 "연결이 끊어졌습니다"가 떴다.

아래는 취향이 아니라 **금지**다.

- **포트 3000·8000을 건드리지 않는다.** 3000은 Pathfinder 프론트엔드, 8000은
  백엔드다. 이 포트를 쓰거나, 조회하거나, 점유한 프로세스를 정리하려 하지 않는다.
  프로토타입에 배정되는 포트는 **4000–8000 범위에서 호스팅이 고른다**(`host.py`의
  `_scan_port`) — 네가 정하는 것이 아니다.
- **다른 프로세스를 죽이지 않는다.** `pkill`, `killall`, `kill -9 $(lsof -ti:...)`,
  `fuser -k` 류를 쓰지 않는다. 네가 **직접 띄운** 프로세스만 정리한다.
- **브라우저 자동화(Playwright·Puppeteer·chrome-headless)를 실행하지 않는다.**
  화면 확인은 사용자가 프로토타입 탭의 라이브 프리뷰로 한다. 스크린샷을 찍기 위해
  브라우저를 띄우지 않는다. `npx playwright ...`, `npm run test:e2e`도 해당한다 —
  리포의 `playwright.config.ts`는 **포트 3000을 겨냥하므로** 실행하면 위 사고가
  그대로 재현된다.
- **개발/프로덕션 서버를 띄운 채로 턴을 끝내지 않는다.** 빌드 검증은
  `npm run build`로 한다. 런타임 확인이 꼭 필요하면 아래 규율을 지킨다.

### 서버를 꼭 띄워야 한다면

`npm run start`/`dev`는 **npm이 실제 서버를 자식으로 띄운다.** 그래서 job
스펙(`kill %1`)이나 npm의 PID만 죽이면 **실제 리스너가 고아로 남아 포트를 계속
점유한다** — 우리 호스팅 코드도 같은 이유로 프로세스 그룹에 신호를 보낸다
(`host.py`의 `stop()`). 반드시 이렇게 한다:

```bash
# 1) 4000-8000 밖의 임의 포트 + 자기만의 프로세스 그룹으로 띄운다
setsid npm run start > /tmp/smoke.log 2>&1 &
PGID=$!
sleep 6
curl -s http://localhost:9123/... || true      # 검증

# 2) 그룹째 정리한다. 이 줄을 빼면 서버가 남는다
kill -- -"$PGID" 2>/dev/null || true
wait "$PGID" 2>/dev/null || true
```

`kill %1 2>/dev/null` 같은 형태를 복사해 쓰지 않는다. 실제로 이 패턴이 서버를
남겼고, 심지어 `kill %12>/dev/null`(→ `kill %12`로 파싱되어 아무것도 죽이지 않는
오타)까지 나왔다. 리다이렉트를 붙일 때 **`%1`과 `2>`가 붙어버리지 않도록** 반드시
공백을 둔다.

## Bedrock 호출 코드 — 샘플링 파라미터를 보내지 않는다

프로토타입 코드에서 Bedrock Claude를 호출할 때 **`temperature`, `top_p`,
`top_k`를 보내지 않는다.** Claude Opus 4.7 이후 모델(Opus 4.7·4.8·5, Sonnet 5)은
이 파라미터들을 제거했고, 보내면 요청 자체가 실패한다 — 이 배포의
`ap-northeast-2`에서 실측한 에러다:

```
ValidationException: The model returned the following errors:
  `temperature` is deprecated for this model.
```

Converse API의 `inferenceConfig`에는 **`maxTokens`만** 넣는다:

```js
const inferenceConfig = { maxTokens };   // temperature/topP를 넣지 않는다
```

**모델별로 분기하지 말고 아예 보내지 않는다.** 모델 ID를 정규식으로 검사해
특정 모델만 제외하는 우회 코드는 만들지 않는다 — 기본 모델은 환경변수로 바뀌고,
그때마다 정규식이 새 모델을 놓쳐 같은 에러가 다시 난다(실제로 한 번 겪었다:
`opus-(4-8|5)`만 잡는 패턴이 `sonnet-5`를 놓쳤다). 출력의 결정성이나 다양성이
필요하면 프롬프트로 지시한다.

같은 이유로 **`budget_tokens`(extended thinking)도 보내지 않는다** — Opus 4.7
이후 제거됐다. 추론 깊이가 필요하면 `additionalModelRequestFields`의
`thinking: {type: "adaptive"}`를 쓴다.

예외: Sonnet 4.6 이하는 `temperature`를 아직 받는다. 그래도 위 규칙을 따른다 —
어느 모델에서도 동작하는 코드가 목적이다.

## NextJS는 15 버전을 사용한다.

## 프로토타입은 하위 경로에서 서빙된다 — basePath 필수

프로토타입은 루트가 아니라 `/proto/{project_id}/{slug}/` 하위에서 리버스 프록시로
서빙된다. 그 프리픽스는 호스팅이 빌드 시점에 `NEXT_PUBLIC_BASE_PATH` 환경변수로
넘긴다(프레임워크 중립 별칭 `PROTO_BASE_PATH`도 같은 값).

**Next.js 프로토타입은 `next.config.js`(또는 `.ts`/`.mjs`)에 아래를 반드시
포함한다:**

```js
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  basePath,
  // 프리픽스가 붙은 자산 URL을 그대로 쓰게 한다. basePath만으로도 _next/ 자산은
  // 덮이지만, 명시해 두면 의도가 드러난다.
  assetPrefix: basePath || undefined,
  // 프록시와 같은 방향으로 정규화한다. 아래 "trailingSlash" 절 참조 —
  // 빼면 리다이렉트 무한 루프(ERR_TOO_MANY_REDIRECTS)가 난다.
  trailingSlash: true,
};

export default nextConfig;
```

`basePath`를 빼면 **빌드 산출물의 자산 URL이 루트로 굳는다** — Next.js는 이 값을
런타임이 아니라 빌드 시점에 인라인하므로, 배포 후에는 고칠 수 없고 화면이
`/_next/static/...` 404로 깨진다. 프리픽스가 없는 환경(로컬 단독 실행)에서는
환경변수가 없어 `""`가 되므로 그대로 동작한다.

**하드코딩 금지.** 프리픽스를 `next.config.js`에 문자열로 박지 말고 위처럼
환경변수에서 읽는다 — slug가 바뀌면 값도 바뀐다.

**경로를 직접 쓸 때:** `<Link href="/about">`이나 `router.push("/about")`는
Next.js가 `basePath`를 자동으로 붙이므로 그대로 둔다. 반면 `fetch("/api/...")`,
`<img src="/logo.png">`처럼 프레임워크를 거치지 않는 참조는 자동 처리되지 않으니,
`` `${basePath}/...` `` 형태로 조립하거나 Next.js의 `<Image>`를 쓴다.

**Next.js가 아닌 경우** (Vite, CRA 등): 같은 값을 `PROTO_BASE_PATH`로 받는다.
Vite는 `base`, CRA는 `PUBLIC_URL`이 대응하는 설정이다.

## trailingSlash: true — 빼면 리다이렉트 무한 루프가 난다

`basePath`와 **같은 비중으로 필수다.** 빠뜨리면 화면이 아예 열리지 않는다:

```
This page isn't working
... redirected you too many times.
ERR_TOO_MANY_REDIRECTS
```

**원인은 두 정규화가 서로 반대 방향이라는 것이다.** 프록시와 프로토타입이 같은
URL을 두고 각자 "올바른 형태"로 되돌리려 하면서 서로의 결과를 무효화한다:

| 주체 | 규칙 |
|---|---|
| Pathfinder 프록시 | 슬래시 **없음 → 있음** (`/proto/{pid}/{slug}` → `/proto/{pid}/{slug}/`) |
| Next.js 기본값(`trailingSlash: false`) | 슬래시 **있음 → 없음** |

실측한 순환(프록시 코드로 재현):

```
브라우저  /api/proto/p1/demo/
  → 프로토타입이 308 → /api/proto/p1/demo      (Next가 슬래시 제거)
브라우저  /api/proto/p1/demo
  → 프록시가 307   → /api/proto/p1/demo/       (프록시가 슬래시 추가)
  → 무한 반복
```

**프록시 쪽을 바꿀 수는 없다.** 프록시가 슬래시를 붙이는 이유는 상대 경로 자산
참조다 — 슬래시 없는 `.../{slug}`에서 브라우저는 `href="styles.css"`를
`.../{pid}/`(slug가 빠진 경로) 기준으로 풀어 모든 자산이 502가 된다. 슬래시를
붙여야 문서의 base가 `.../{slug}/`가 되어 상대 참조가 프로토타입 안에 떨어진다.

그래서 **맞추는 쪽은 프로토타입이다.** `trailingSlash: true`를 넣으면 Next도
프록시와 같은 방향(슬래시 있는 형태)으로 정규화하므로 순환이 생기지 않는다.

`basePath`처럼 빌드 시점에 굳는 설정이므로, 빠뜨린 프로토타입은 **재빌드해야
고쳐진다** — 배포 후에는 손댈 수 없다.

**Next.js가 아닌 경우:** 같은 성질의 설정을 찾아 슬래시 있는 형태로 맞춘다. 정적
서버(`serve`, `http-server` 등)는 대개 디렉토리 URL을 그대로 다루므로 별도 설정이
필요 없지만, SPA 라우터가 자체적으로 URL을 정규화한다면 슬래시를 **제거하지 않도록**
설정한다.
