// 인증 seam. 세션은 httpOnly 쿠키(aipds_access)에 있고 JS는 읽을 수 없으므로,
// 클라이언트가 할 일은 fetch에 쿠키를 실으라고 알리는 것뿐이다. same-origin
// /api 프록시가 그 쿠키를 Authorization: Bearer로 번역한다(lib/api/proxyAuth.ts).
//
// 이 상수가 리터럴이 아닌 이유: 세 클라이언트 파일이 같은 값을 쓰고, 흩뿌리면
// 정책을 바꿀 때 한 곳을 놓친다.
export const CREDENTIALS: RequestCredentials = "include";
