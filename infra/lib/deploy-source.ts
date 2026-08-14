// infra/lib/deploy-source.ts — 배포되는 코드가 무엇인지 정하는 곳.
//
// 종전에는 리포 루트를 CDK 에셋(zip)으로 올렸다. 두 가지가 문제였다:
//
//   1. **배포되는 것이 커밋된 코드가 아니라 워킹 트리였다.** 미커밋 변경이 그대로
//      올라가므로 "지금 도는 것이 어느 커밋인가"에 답할 수 없었다.
//   2. **에셋은 gitignore된 파일까지 싣는다.** 그래서 app-asset-excludes.json이라는
//      보정 목록이 필요했고, 그 목록에서 빠진 것이 실제로 두 번 사고를 냈다
//      (개발용 .claude/CLAUDE.md가 에이전트 컨텍스트에 조상으로 들어간 것,
//      개발 박스의 proto-type/이 배포에 실려 "빌드 완료"로 보인 것).
//
// 리포가 공개된 뒤로는 인스턴스가 직접 clone할 수 있다. clone은 **tracked 파일만**
// 가져오므로 2번의 실패 종류가 사라지고, 미커밋 변경이 배포되지 않으므로 1번도
// 사라진다.
//
// **커밋 SHA 고정에서 브랜치로 되돌린 이유.** 종전에는 synth 시점에
// `git rev-parse HEAD`로 SHA를 구해 user-data에 박고 `checkout --detach`했다. 그
// 방식은 배포자에게 "이 커밋을 푸시했는가"를 계속 요구했고 — 안 했으면 cdk deploy는
// 성공하고 **EC2 부팅만** 실패한다 — CDK_DEPLOY_REF, synth 시점 푸시 여부 판정,
// 오타 경고가 전부 그 하나의 실수를 막기 위해 존재했다. 지금은 인스턴스가 부팅
// 시점의 **origin/main 최신 커밋**을 쓴다: synth가 git을 호출하지 않고, 배포되는
// 것은 언제나 "푸시된 main"이므로 그 실수 자체가 성립하지 않는다.
//
// **대가: `cdk deploy`가 코드 갱신 수단이 아니다.** user-data 문자열에 SHA가 없어
// 커밋을 밀어도 user-data가 바이트 단위로 같고, 그러면 CloudFormation이 인스턴스를
// 교체하지 않는다(UserData는 replacement 속성). 그래서 코드 갱신은 인스턴스 위의
// `pathfinder-update`가 담당한다(lib/user-data.ts가 부팅 시 설치한다) — SSM으로
// 들어가 `sudo pathfinder-update` 한 줄이면 최신 main을 당겨 필요한 것만 다시
// 빌드하고 서비스를 재시작한다. 인스턴스 교체(5~10분 502)가 없어 워크숍 중에도
// 쓸 수 있다는 것이 이 방향의 실질적인 이득이다.
//
// 무엇이 도는지는 인스턴스에서 `git -C /opt/pathfinder rev-parse HEAD`로 본다
// (부팅 시점의 커밋은 부트스트랩 로그에도 한 줄로 남는다).
//
// 대가 하나 더: 부팅이 GitHub에 도달해야 한다.

/** 공개 리포. HTTPS이므로 인스턴스에 자격증명이 필요 없다. */
export const REPO_URL = 'https://github.com/muylucir/ai-plc-pathfinder.git';

/**
 * 배포 대상 브랜치. 인스턴스는 부팅 때, `pathfinder-update`는 실행될 때 이
 * 브랜치의 원격 최신 커밋으로 맞춘다.
 *
 * 여기에 커밋 SHA를 넣지 말 것 — 그러면 `pathfinder-update`가 갱신할 것이 없는
 * 고정 배포로 되돌아간다. 다른 브랜치를 쓰려면 이 값을 바꾼다(한 줄이다).
 */
export const DEPLOY_BRANCH = 'main';
