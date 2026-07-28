# 모든 대화는 한국어로 진행
# 프로토타입을 생성할때 디자인은 shadcn-design 스킬을 사용

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
