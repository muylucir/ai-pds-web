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
// 가져오므로 2번의 실패 종류가 사라지고, 커밋을 고정하면 1번도 사라진다.
//
// 대가: 부팅이 GitHub에 도달해야 하고, 커밋하지 않은 변경은 배포할 수 없다.
import { execFileSync } from 'node:child_process';

/** 공개 리포. HTTPS이므로 인스턴스에 자격증명이 필요 없다. */
export const REPO_URL = 'https://github.com/muylucir/ai-plc-pathfinder.git';

/** `CDK_DEPLOY_REF`로 배포 커밋을 명시할 때 쓰는 환경변수 이름. */
export const DEPLOY_REF_ENV = 'CDK_DEPLOY_REF';

/**
 * 배포할 커밋.
 *
 * 이 값은 user-data 문자열에 들어가므로 **배포의 결정성과 인스턴스 교체 여부를
 * 동시에 정한다.** 브랜치 이름(`main`)을 넣으면 안 되는 이유가 그것이다: user-data
 * 내용이 바뀌지 않아 `cdk deploy`가 인스턴스를 교체하지 않고, 그러면 배포가 코드
 * 갱신 수단이 아니게 된다. 커밋 SHA를 넣으면 커밋마다 user-data가 달라져
 * CloudFormation이 인스턴스를 교체한다(UserData는 replacement 속성이다).
 *
 * 순서:
 *   1. `CDK_DEPLOY_REF`가 있으면 그 값 (CI·롤백·특정 커밋 재배포)
 *   2. 없으면 로컬 `git rev-parse HEAD`
 *   3. 둘 다 안 되면 **던진다.** 브랜치 이름으로 조용히 떨어지지 않는다 —
 *      그 폴백은 위의 "교체가 안 일어난다"를 증상 없이 되살린다.
 */
export function resolveDeployRef(): string {
  const explicit = process.env[DEPLOY_REF_ENV]?.trim();
  if (explicit) return explicit;

  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: __dirname,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    throw new Error(
      `cannot determine the commit to deploy: 'git rev-parse HEAD' failed and ` +
      `${DEPLOY_REF_ENV} is not set.\n` +
      `Set it explicitly, e.g. ${DEPLOY_REF_ENV}=<sha> npx cdk deploy --all`,
    );
  }
}

/** git이 이 환경에 아예 없는지. 없으면 검사 자체를 포기한다(경고 없음). */
function gitMissing(err: unknown): boolean {
  return (err as { code?: string } | null)?.code === 'ENOENT';
}

function git(args: string[]): { ok: boolean; out: string; missing: boolean } {
  try {
    const out = execFileSync('git', args, {
      cwd: __dirname,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return { ok: true, out: out.trim(), missing: false };
  } catch (err) {
    return { ok: false, out: '', missing: gitMissing(err) };
  }
}

/**
 * 배포 대상을 EC2가 실제로 clone할 수 있는지 미리 본다.
 *
 * clone은 **부팅 시점**에 일어난다. 그래서 이 실수는 `cdk deploy` 성공 +
 * **EC2 부팅 실패**로 나타난다 — 화면에는 502만 남고 원인은 cloud-init 로그에만
 * 있다(`fatal: reference is not a tree`). 여기서 미리 알린다.
 *
 * 두 가지를 구분해서 본다. 하나로 묶으면 더 흔한 쪽을 놓친다:
 *
 *   1. **로컬에도 없는 커밋** — CDK_DEPLOY_REF 오타나 fetch하지 않은 SHA.
 *      `git branch --contains`가 에러로 끝나므로, 이것을 그냥 삼키면 오타가
 *      아무 경고 없이 배포된다.
 *   2. **로컬에는 있지만 푸시되지 않은 커밋** — 가장 흔한 경우다. 명령은
 *      성공하고 출력이 비어 있다.
 *
 * git이 없으면 조용히 통과시킨다(오프라인/컨테이너 synth를 막을 이유가 없다).
 */
export function warnIfRefNotPushed(ref: string): string | null {
  const exists = git(['rev-parse', '--verify', '--quiet', `${ref}^{commit}`]);
  if (exists.missing) return null;
  if (!exists.ok || !exists.out) {
    return `${ref} is not a commit in this repository — check ${DEPLOY_REF_ENV} for a typo, `
      + 'or fetch it first. EC2 clones at boot, so this fails the instance, not the deploy.';
  }

  const onRemote = git(['branch', '--remotes', '--contains', exists.out]);
  if (onRemote.missing) return null;
  // 명령이 실패했다면(원격 추적 브랜치가 없는 리포 등) 판정하지 않는다.
  if (!onRemote.ok) return null;
  if (onRemote.out) return null;

  return `${ref} is not on any remote branch — push it first. EC2 clones at boot, `
    + 'so an unpushed commit fails the instance, not the deploy.';
}
