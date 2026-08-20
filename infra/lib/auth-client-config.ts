// 앱 클라이언트 설정의 단일 출처.
//
// 왜 별도 모듈인가: 콜백 URL은 배포 시점에 정해지는 CloudFront 도메인을 포함해야
// 하는데, AuthStack이 만든 클라이언트를 HostingStack이 UpdateUserPoolClient로
// 갱신한다. 그 API는 PUT 시맨틱이어서 지정하지 않은 필드를 지우므로, 두 곳이
// 같은 값을 봐야 한다. 스택 간 import는 순환을 만들기 때문에 순수 상수 모듈로 뺀다.

export const SEED_ADMIN_EMAIL = 'admin@aipds.local';
export const SEED_PM_EMAIL = 'pm@aipds.local';

// 데모/워크숍용 사전 설정 비밀번호. ⚠️ CloudFormation 템플릿과 스택 이벤트에
// 평문으로 남는다 — 운영 전환 시 반드시 교체한다(스펙 §4.1).
export const SEED_PASSWORD = 'AiPdsWeb2026@!';

export const GROUP_ADMIN = 'admin';
export const GROUP_PM = 'pm';

// 프론트 route handler / 로그인 화면의 실제 경로와 반드시 일치해야 한다.
export const CALLBACK_PATH = '/api/auth/callback';
export const LOGOUT_PATH = '/login';

export const LOCAL_APP_URL = 'http://localhost:3000';
export const OAUTH_SCOPES = ['openid', 'email', 'profile'];

// 앱 클라이언트 이름. 콘솔에 뜨는 이름이라 값 자체는 사소하지만, 다른 PUT
// 필드들과 같은 이유로 여기 둔다 — AuthStack만 알고 HostingStack의 재전송이
// 모르면 재배포마다 Cognito가 만든 기본 이름으로 조용히 바뀐다.
export const CLIENT_NAME = 'pathfinder-web';

// 토큰 유효기간 (분 단위). AuthStack의 client 정의와 HostingStack의
// UpdateUserPoolClient 재전송이 반드시 같은 값을 써야 한다 — PUT 시맨틱이라
// 어긋나면 재배포 때마다 유효기간이 조용히 리셋된다. AuthStack은 이 값을
// cdk.Duration.minutes()로 감싸 넘기고, HostingStack은 그대로 정수로 보낸다.
// access/id가 3시간인 이유는 **프로토타입 빌드 한 번**이다. 빌드는 보통 1시간
// 이내인데, 종전 값(60분)은 그 경계와 정확히 겹쳐 여유가 0이었다.
//
// 만료의 1차 원인은 수명이 아니라 갱신 기회의 부재였다: 토큰 갱신은 /api
// 프록시가 백엔드 401을 받았을 때만 발동하는데(app/api/[...path]/route.ts),
// `GET /events`가 200으로 열린 뒤 수십 분을 사는 SSE 연결은 그 401을 다시
// 만들지 못한다. 그래서 빌드가 도는 동안 갱신 기회가 하나도 없었다. 그쪽은
// 주기 갱신으로 고쳤다(frontend/lib/auth/keepSessionAlive.ts + /api/auth/refresh).
//
// 이 값은 **두 번째 방어선**이다. 주기 갱신이 몇 번 연속 실패해도(네트워크
// 단절, Cognito 일시 오류) 빌드 한 번이 한 토큰 수명 안에서 끝나면 사용자는
// 만료를 겪지 않는다. 두 계층이 필요한 이유는 서로 다른 실패를 막기 때문이다:
// 갱신은 3시간을 넘는 세션을, 이 수명은 갱신 자체의 실패를 덮는다.
//
// 24시간(Cognito 상한)까지 늘리지 않는 이유는 탈취된 토큰의 유효 창이 그만큼
// 길어지기 때문이다. 3시간은 빌드를 덮으면서 그 창을 실무적으로 짧게 유지한다.
// infra/test/auth-client-config.assert.ts가 이 근거를 불변식으로 지킨다.
export const ACCESS_TOKEN_VALIDITY_MINUTES = 180; // 3시간
export const ID_TOKEN_VALIDITY_MINUTES = 180; // 3시간 — access와 같아야 한다
export const REFRESH_TOKEN_VALIDITY_MINUTES = 60 * 24 * 30; // 30일

// AuthStack의 client가 명시적으로 ALLOW_REFRESH_TOKEN_AUTH만 켜는 이유는
// pathfinder-auth-stack.ts의 authFlows 설정(userSrp/userPassword 둘 다 false)에
// 있다 — CDK가 그 외에는 아무 플래그도 안 켜면서도 refreshTokenRotationGracePeriod를
// 지정하지 않았을 때 이 플로우 하나만 자동으로 추가한다. /api 프록시의 401 리프레시
// 경로가 이 플로우에 의존하므로, UpdateUserPoolClient 재전송에서 빠지면 재배포마다
// 리프레시가 조용히 끊긴다.
export const EXPLICIT_AUTH_FLOWS = ['ALLOW_REFRESH_TOKEN_AUTH'];

function join(appUrl: string, path: string): string {
  return `${appUrl.replace(/\/$/, '')}${path}`;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function callbackUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, CALLBACK_PATH)));
}

export function logoutUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, LOGOUT_PATH)));
}

/**
 * 이메일 → Cognito Username(로컬파트).
 *
 * 이 풀은 AliasAttributes=[email]이다. Cognito는 그 설정에서 **이메일 형식
 * Username을 거부한다** — "Username cannot be of email format, since user pool
 * is configured for email alias". 이메일이 alias로 예약되어 있어 username과
 * 충돌하기 때문이다. 실측: 이 규칙을 몰라 시드 계정 생성이 스택 롤백을 냈다.
 *
 * 그래서 '@' 앞부분만 Username으로 쓴다. 사용자는 어느 쪽이든 이메일로
 * 로그인한다(email alias가 그 일을 한다). UUID 자동 생성
 * (UsernameAttributes)을 쓰지 않는 이유는 README의 "username == 이메일" 절과
 * 같다 — 재배포마다 값을 알 수 없어 시딩이 비결정적이 된다.
 *
 * ⚠️ **backend/pathfinder/auth/cognito.py의 username_for_email과 같은 규칙이어야
 * 한다.** 어긋나면 시드 계정과 초대 계정의 Username 규칙이 갈리고, 재배포 시
 * 시드가 기존 사용자를 못 찾아 중복 계정을 만든다.
 *
 * ⚠️ 로컬파트가 같고 도메인만 다른 두 계정(kim@a.com / kim@b.com)은 같은
 * Username으로 충돌한다 — 워크숍 규모(단일 도메인)에서 감수한 트레이드오프다.
 */
export function usernameForEmail(email: string): string {
  const local = email.trim().toLowerCase().split('@')[0];
  // Cognito Username에 허용되는 문자는 제한적이다(예: 태그 주소의 '+'는 불가).
  const safe = local.replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  if (!safe) {
    throw new Error(`cannot derive a Cognito username from email: ${email}`);
  }
  return safe;
}
