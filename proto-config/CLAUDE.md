# 모든 대화는 한국어로 진행
# 프로토타입을 생성할때 디자인은 shadcn-design 스킬을 사용

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
