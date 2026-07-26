// 앱 클라이언트 설정의 단일 출처.
//
// 왜 별도 모듈인가: 콜백 URL은 배포 시점에 정해지는 CloudFront 도메인을 포함해야
// 하는데, AuthStack이 만든 클라이언트를 HostingStack이 UpdateUserPoolClient로
// 갱신한다. 그 API는 PUT 시맨틱이어서 지정하지 않은 필드를 지우므로, 두 곳이
// 같은 값을 봐야 한다. 스택 간 import는 순환을 만들기 때문에 순수 상수 모듈로 뺀다.

export const SEED_ADMIN_EMAIL = 'admin@pathfinder.local';
export const SEED_PM_EMAIL = 'pm@pathfinder.local';

// 데모/워크숍용 사전 설정 비밀번호. ⚠️ CloudFormation 템플릿과 스택 이벤트에
// 평문으로 남는다 — 운영 전환 시 반드시 교체한다(스펙 §4.1).
export const SEED_PASSWORD = 'PathFinder2026!@';

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
export const ACCESS_TOKEN_VALIDITY_MINUTES = 60; // 1시간
export const ID_TOKEN_VALIDITY_MINUTES = 60; // 1시간
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
