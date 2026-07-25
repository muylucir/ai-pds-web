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
