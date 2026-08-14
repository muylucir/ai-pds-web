// infra/test/deployed-tree.assert.ts
//
// **/opt/pathfinder가 될 트리에 있으면 안 되는 것이 없는지** 확인한다.
//
// 이 테스트는 app-asset.assert.ts를 대체한 것이다. 종전에는 코드가 CDK 에셋
// zip으로 갔으므로 synth해서 스테이징된 디렉터리를 들여다봤다. 지금은 EC2가
// 리포를 clone하므로(lib/deploy-source.ts) 인스턴스에 올라가는 것은 정확히
// **git이 tracked로 보는 파일**이다 — 그래서 판정 대상이 `git ls-files`다.
// synth가 필요 없어 훨씬 빠르고, 무엇보다 판정이 실제 배포 경로와 같아졌다.
//
// **왜 이 불변식이 있는가(2026-08-04 실측).** 영어를 고른 프로젝트의 워크스페이스
// 채팅이 계속 한국어로 진행됐다. 원인의 절반은 discovery-config였고(그쪽은
// backend/tests/test_agent_language.py가 지킨다), 남은 절반이 이것이다:
//
//   에이전트 cwd     /opt/pathfinder/workspaces/{pid}
//   트리에 있던 것   /opt/pathfinder/.claude/CLAUDE.md   <- **조상**
//
// Claude Code는 cwd에서 위로 올라가며 CLAUDE.md를 전부 로드한다(실제 CLI로
// 확인: `claude --debug -p "로드한 CLAUDE.md 경로를 나열해라"`가 `(ancestor
// project)` 표시와 함께 조상 파일을 낸다). 그래서 리포 개발용 .claude/CLAUDE.md의
// 한국어 한 줄이 영어 프로젝트의 매 턴 컨텍스트에 들어갔다.
//
// **이건 끄는 스위치가 없다.** CLAUDE_CONFIG_DIR은 "user" 레벨만 옮기고, 조상
// 탐색은 "cwd가 앱 트리 안에 있다"는 사실에서 나온다(워크스페이스를 앱 트리에
// 두는 것은 user-data.ts가 백업·권한 때문에 의도한 배치다). 그러므로 그 파일이
// 트리에 없어야 하고, 이 테스트가 그 불변식을 고정한다.
//
// clone 방식이 종전보다 강한 지점: `.claude/`는 .gitignore에 있어 애초에 tracked가
// 아니므로 **목록에서 빼는 것을 잊을 수가 없다.** 에셋 시절에는 gitignore와 무관하게
// 실렸으므로 별도의 제외 목록을 사람이 관리해야 했고, 그 목록에서 빠진 것이 사고를 냈다.
import * as assert from 'node:assert';
import { execFileSync } from 'node:child_process';
import * as path from 'node:path';

const REPO_ROOT = path.join(__dirname, '..', '..');

/** 인스턴스에 올라갈 파일 목록 = git이 tracked로 보는 것. */
function trackedFiles(): string[] {
  const out = execFileSync('git', ['ls-files', '-z'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
  });
  return out.split('\0').filter((s) => s !== '');
}

function testNoUnexpectedClaudeMdInTheDeployedTree() {
  const files = trackedFiles();

  // 1) 리포 개발용 .claude/ 는 tracked가 아니어야 한다.
  const devClaude = files.filter((f) => f === '.claude' || f.startsWith('.claude/'));
  assert.deepStrictEqual(devClaude, [],
    'the repo-development .claude/ must NOT be tracked: it would be cloned to ' +
    '/opt/pathfinder/.claude/, an ANCESTOR of the agent cwd ' +
    '(/opt/pathfinder/workspaces/{pid}), and Claude Code loads ancestor ' +
    'CLAUDE.md files — measured against the real CLI. Its Korean line would ' +
    'then enter every English project\'s context, and there is no runtime ' +
    `switch to turn it off. Found: ${devClaude.join(', ')}`);

  // 2) 트리 어디에도 의도하지 않은 CLAUDE.md가 없어야 한다. 의도된 것은 에이전트
  //    config dir 두 개뿐이다. 새 CLAUDE.md가 리포에 커밋되면 이 단정이 잡는다.
  const ALLOWED = new Set([
    'discovery-config/CLAUDE.md',   // Discovery config dir (언어 중립)
    'proto-config/CLAUDE.md',       // 프로토타입 빌더 config dir
  ]);
  const found = files.filter((f) => path.basename(f) === 'CLAUDE.md');
  for (const rel of found) {
    assert.ok(ALLOWED.has(rel),
      `unexpected tracked CLAUDE.md: ${rel}\n` +
      'Every CLAUDE.md under /opt/pathfinder that is an ancestor of ' +
      '/opt/pathfinder/workspaces/{pid} is loaded into the agent context. If ' +
      'this file is meant to ship, add it to ALLOWED here and make sure it is ' +
      'language-neutral (see backend/tests/test_agent_language.py).');
  }

  console.log(`OK  deployed tree: no dev-only .claude, CLAUDE.md set = [${found.join(', ')}]`);
}

function testNoBuiltPrototypesInTheDeployedTree() {
  const files = trackedFiles();
  // 개발 박스에서 만든 프로토타입 빌드 트리가 올라가면, 아무도 빌드하지 않은
  // 프로토타입이 새 인스턴스에서 "빌드 완료"로 보인다 —
  // proto/session.py의 has_build_output이 보는 것이 정확히 이 경로다.
  // 세션 트랜스크립트·큐도 같은 이유로 올라가면 안 된다.
  const FORBIDDEN_PREFIXES = [
    'proto-type/',
    'protos/',
    'proto-config/projects/',
    'proto-config/sessions/',
    'discovery-config/projects/',
    'discovery-config/sessions/',
  ];
  for (const prefix of FORBIDDEN_PREFIXES) {
    const hits = files.filter((f) => f.startsWith(prefix));
    assert.deepStrictEqual(hits, [],
      `${prefix} must not be tracked — it would be cloned into the app tree and ` +
      `make a prototype nobody built look already built. Found: ${hits.join(', ')}`);
  }
  console.log('OK  deployed tree: no build output or session state is tracked');
}

function testDeployedTreeStillShipsWhatTheRuntimeNeeds() {
  // 대조군: 무언가를 .gitignore로 밀어내다 런타임에 필요한 것을 빠뜨리는 회귀를
  // 막는다. 에셋 시절에는 제외 목록이 과하게 넓어지는 것이 위험이었고, 지금은
  // .gitignore가 그 자리다 — 같은 방향의 실수가 여전히 가능하다.
  const files = new Set(trackedFiles());
  for (const rel of [
    'backend/pathfinder/app.py',
    'frontend/package.json',
    'frontend/package-lock.json',   // npm ci 가 부팅 시 요구한다
    'discovery-config/CLAUDE.md',
    'proto-config/CLAUDE.md',
    'proto-config/skills/shadcn-design/SKILL.md',
    'rule/aiplc-rules/aws-aiplc-rules/core-workflow.md',
    'rule/aiplc-rules/language/ko.md',
    'rule/aiplc-rules/language/en.md',
  ]) {
    assert.ok(files.has(rel),
      `${rel} must be tracked — the instance clones the repo, so anything ` +
      'untracked simply does not exist at /opt/pathfinder');
  }
  console.log('OK  deployed tree: rules, both language directives, both config dirs and the lockfile ship');
}

testNoUnexpectedClaudeMdInTheDeployedTree();
testNoBuiltPrototypesInTheDeployedTree();
testDeployedTreeStillShipsWhatTheRuntimeNeeds();
