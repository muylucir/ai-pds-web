// 별도 스택이 인스턴스에 알리는 값(SSM Parameter Store). 새 인스턴스가 부팅 때 읽는다
// (infra/scripts/aipds-harden `boot`).
//
// **왜 스택 출력을 user-data에 넣지 않는가.** 두 스택은 HostingStack의 인스턴스 롤을 참조한다 —
// user-data가 그 출력을 참조하면 순환이고, 끊으려고 user-data를 바꾸면 HostingStack 배포가
// EC2를 교체한다. 그래서 값은 스택이 파라미터로 쓰고 인스턴스가 실행 시점에 읽는다. 스택이
// 없으면 파라미터도 없고, 부팅은 그 기능을 끈 채로 둔다.
//
// 이름은 aipds-harden에도 상수로 있다 — test/instance-params.assert.ts가 둘을 맞춘다.
export const INSTANCE_PARAMS = {
  /** AipdsAgentCredsStack의 AgentRole. 있으면 부팅 때 래퍼를 켜고 IMDS를 막는다. */
  agentRoleArn: '/aipds/agent-role-arn',
  /** AipdsPreviewStack의 프리뷰 오리진과 비밀 헤더 시크릿 ARN. */
  previewOrigin: '/aipds/preview-origin',
  previewSecretArn: '/aipds/preview-secret-arn',
} as const;
