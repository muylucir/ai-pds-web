// infra/test/deployed-tree.assert.ts
//
// **/opt/aipds가 될 트리에 있으면 안 되는 것이 없는지** 확인한다.
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
//   에이전트 cwd     /opt/aipds/workspaces/{pid}
//   트리에 있던 것   /opt/aipds/.claude/CLAUDE.md   <- **조상**
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
import * as fs from 'node:fs';
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
    '/opt/aipds/.claude/, an ANCESTOR of the agent cwd ' +
    '(/opt/aipds/workspaces/{pid}), and Claude Code loads ancestor ' +
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
      'Every CLAUDE.md under /opt/aipds that is an ancestor of ' +
      '/opt/aipds/workspaces/{pid} is loaded into the agent context. If ' +
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
    'backend/aipds/app.py',
    'frontend/package.json',
    'frontend/package-lock.json',   // npm ci 가 부팅 시 요구한다
    'discovery-config/CLAUDE.md',
    'proto-config/CLAUDE.md',
    'proto-config/skills/shadcn-design/SKILL.md',
    // 룰셋 자체는 이제 파일이 아니라 서브모듈이다 —
    // testTheRulesetShipsAsASubmodule()이 본다. 여기서는 그 서브모듈을 되살릴 수
    // 있게 하는 파일만 본다: .gitmodules가 없으면 `submodule update --init`이
    // 아무것도 하지 않고 조용히 성공한다.
    '.gitmodules',
    // 언어 지시는 2026-08-18에 룰셋 트리 밖으로 나왔다 — 업스트림 aiplc-rules/에는
    // language/ 가 없으므로, 룰셋을 통째로 갈아 끼울 때 우리 콘텐츠가 함께
    // 사라지지 않아야 한다.
    //
    // **그 뒤 2026-08-19(8b58cba)에 파일에서 코드로 다시 옮겼다** —
    // `language/{ko,en}.md` 두 파일이 `workspace_rules.LANGUAGE_DIRECTIVES`
    // 상수가 됐다. 이 목록은 그것을 따라가지 못해 지운 파일을 계속 요구했고,
    // 그날부터 이 단정이 실패했다(런타임 영향은 없다 — 부팅이 그 파일을 필요로
    // 하지 않는다). backend/tests/test_workspace_rules.py의
    // `test_the_language_directive_is_code_not_a_file`이 정반대를 단정하므로,
    // 두 테스트가 서로 모순한 상태였다.
    //
    // 불변식은 그대로다: **언어 지시가 인스턴스에 실린다.** 실리는 자리만
    // 파일에서 이 모듈로 바뀌었다.
    'backend/aipds/agent/workspace_rules.py',
  ]) {
    assert.ok(files.has(rel),
      `${rel} must be tracked — the instance clones the repo, so anything ` +
      'untracked simply does not exist at /opt/aipds');
  }
  console.log('OK  deployed tree: rules, the language directives, both config dirs and the lockfile ship');
}

function testTheRulesetShipsAsASubmodule() {
  // **왜 별도 테스트인가.** 위 목록의 판정은 "tracked인가"이고, 서브모듈에는 그것이
  // 충분하지 않다. gitlink는 tracked인데도 `git clone`이 내용을 가져오지 않으므로,
  // tracked만 확인하면 인스턴스에 빈 디렉터리가 올라가는 것을 통과시킨다.
  //
  // 룰셋을 서브모듈로 둔 이유(2026-08-21): 종전에는 상류 aiplc-rules/를 리포에
  // 복사해 두었고, 실제로 세 파일이 갈라졌다(c806343이 모델 ID를 고쳤다). 상류가
  // 정본이므로 그 갈라짐은 우리 쪽 결함이다 — 사본이 없으면 갈라질 수 없다.
  const staged = execFileSync('git', ['ls-files', '--stage', '-z', 'steering-files'], {
    cwd: REPO_ROOT, encoding: 'utf8',
  }).split('\0').filter(Boolean);
  assert.strictEqual(staged.length, 1,
    'steering-files must be exactly one index entry (the submodule gitlink), got: '
    + JSON.stringify(staged));
  assert.match(staged[0], /^160000 /,
    'steering-files must be a gitlink (mode 160000), not ordinary tracked files — mode '
    + `was ${staged[0].split(' ')[0]}. If the rules got committed as files again, the copy `
    + 'can drift from aws-samples/sample-ai-plc, which is what the submodule exists to prevent');

  // 사본이 되살아나는 것을 막는다. 이 경로는 2026-08-21까지 룰셋의 자리였고,
  // 되돌리는 실수의 형태는 "steering-files/를 그대로 두고 rule/도 다시 만드는 것"이다.
  const revived = trackedFiles().filter((f) => f.startsWith('rule/'));
  assert.deepStrictEqual(revived, [],
    'the AI-PLC ruleset must live only in the steering-files/ submodule; a second copy '
    + `under rule/ is the drift this replaced. Found: ${revived.join(', ')}`);

  // 여기까지는 인덱스만 봤다. 마지막으로 워킹 트리에 내용이 있는지 본다 — 이
  // 단정이 실패하는 흔한 경우는 서브모듈을 초기화하지 않은 fresh clone이고,
  // 그 상태는 부팅해도 룰이 없는 상태와 정확히 같다.
  assert.ok(fs.existsSync(path.join(
    REPO_ROOT, 'steering-files', 'aiplc-rules', 'aws-aiplc-rules', 'core-workflow.md')),
    'steering-files/ is empty — run: git submodule update --init --recursive');
  console.log('OK  deployed tree: the AI-PLC ruleset is an unmodified upstream submodule');
}

testNoUnexpectedClaudeMdInTheDeployedTree();
testNoBuiltPrototypesInTheDeployedTree();
testDeployedTreeStillShipsWhatTheRuntimeNeeds();
testTheRulesetShipsAsASubmodule();
