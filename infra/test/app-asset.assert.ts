// infra/test/app-asset.assert.ts
//
// 배포 zip(리포 에셋)에 **런타임 트리에 있으면 안 되는 것**이 실리지 않는지
// 확인한다. 템플릿의 에셋 해시만 보면 알 수 없으므로, 실제로 synth해서 CDK가
// 스테이징한 디렉터리를 들여다본다 — 그게 인스턴스의 /opt/pathfinder가 될
// 트리다.
//
// **왜 이 테스트가 생겼는가(2026-08-04).** 영어를 고른 프로젝트의 워크스페이스
// 채팅이 계속 한국어로 진행됐다. 원인의 절반은 discovery-config였고(그쪽은
// backend/tests/test_agent_language.py가 지킨다), 남은 절반이 이것이다:
//
//   에이전트 cwd   /opt/pathfinder/workspaces/{pid}
//   에셋에 실린 것 /opt/pathfinder/.claude/CLAUDE.md   <- **조상**
//
// Claude Code는 cwd에서 위로 올라가며 CLAUDE.md를 전부 로드한다. 실제 CLI로
// 실측했다 — cwd를 자손에 두고 `claude --debug -p "로드한 CLAUDE.md 경로를
// 나열해라"`를 돌리면 `(ancestor project)` 표시와 함께 조상 파일이 나온다.
// 그래서 리포 개발용 .claude/CLAUDE.md의 한국어 한 줄이 영어 프로젝트의 매 턴
// 컨텍스트에 들어갔다.
//
// **이건 끄는 스위치가 없다.** CLAUDE_CONFIG_DIR은 "user" 레벨만 옮기고, 조상
// 탐색은 "cwd가 앱 트리 안에 있다"는 사실에서 나온다(워크스페이스를 앱 트리에
// 두는 것은 user-data.ts가 백업·권한 때문에 의도한 배치다). 그러므로 에셋에서
// 빼는 것이 유일하게 확실한 차단이며, 이 테스트가 그 불변식을 고정한다.
import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

/** synth한 뒤 앱 코드 에셋이 스테이징된 디렉터리를 찾는다.
 *
 * 에셋 디렉터리는 여러 개다(람다 번들 등). 리포 루트 에셋은 backend/와
 * frontend/를 함께 가진 유일한 것이므로 그것으로 식별한다 — 해시를 하드코딩하면
 * 리포가 바뀔 때마다 이 테스트가 무의미하게 깨진다. */
function stagedAppAsset(): string {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'pf-asset-'));
  const app = new cdk.App({ outdir });
  // hosting-stack.assert.ts의 makeHosting()과 같은 조립이다 —
  // cfPrefixListId를 주입해 fromLookup(크리덴셜 필요)을 우회한다.
  const drill = new PathfinderDrillStack(app, 'Drill', { env: ENV });
  const auth = new PathfinderAuthStack(app, 'Auth', { env: ENV });
  new PathfinderHostingStack(app, 'Hosting', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  app.synth();

  const candidates = fs.readdirSync(outdir)
    .filter((n) => n.startsWith('asset.'))
    .map((n) => path.join(outdir, n))
    .filter((p) => fs.statSync(p).isDirectory()
      && fs.existsSync(path.join(p, 'backend'))
      && fs.existsSync(path.join(p, 'frontend')));

  assert.strictEqual(candidates.length, 1,
    `expected exactly one staged repo asset, found ${candidates.length} — ` +
    'if the asset layout changed, fix this finder rather than deleting the test');
  return candidates[0];
}

function testAppAssetExcludesDevOnlyClaudeConfig() {
  const root = stagedAppAsset();

  // 1) .claude 자체가 없어야 한다.
  const dotClaude = path.join(root, '.claude');
  assert.ok(!fs.existsSync(dotClaude),
    '.claude must NOT ship in the deploy asset: it lands at ' +
    '/opt/pathfinder/.claude/, an ANCESTOR of the agent cwd ' +
    '(/opt/pathfinder/workspaces/{pid}), and Claude Code loads ancestor ' +
    'CLAUDE.md files — measured against the real CLI. Its Korean line then ' +
    'enters every English project\'s context, and there is no runtime switch ' +
    'to turn it off.');

  // 2) 한 걸음 더: 트리 어디에도 우리가 의도하지 않은 CLAUDE.md가 없어야 한다.
  //    의도된 것은 config dir 두 개와 룰 마스터뿐이다. 새 CLAUDE.md가 리포에
  //    생겨 조용히 실려도 이 단정이 잡는다.
  const ALLOWED = new Set([
    'discovery-config/CLAUDE.md',   // Discovery config dir (언어 중립)
    'proto-config/CLAUDE.md',       // 프로토타입 빌더 config dir
  ]);
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name === '.venv') continue;
        walk(full);
      } else if (e.name === 'CLAUDE.md') {
        found.push(path.relative(root, full));
      }
    }
  };
  walk(root);

  for (const rel of found) {
    assert.ok(ALLOWED.has(rel),
      `unexpected CLAUDE.md in the deploy asset: ${rel}\n` +
      'Every CLAUDE.md under /opt/pathfinder that is an ancestor of ' +
      '/opt/pathfinder/workspaces/{pid} is loaded into the agent context. If ' +
      'this file is meant to ship, add it to ALLOWED here and make sure it is ' +
      'language-neutral (see backend/tests/test_agent_language.py).');
  }

  console.log(`OK  asset: no dev-only .claude, CLAUDE.md set = [${found.join(', ')}]`);
}

function testAppAssetStillShipsWhatTheRuntimeNeeds() {
  // 대조군: 제외 목록이 과하게 넓어져 런타임에 필요한 것을 지우는 회귀를 막는다.
  // ('.claude'를 '**/.claude*' 류로 바꾸면 discovery-config가 날아갈 수 있다.)
  const root = stagedAppAsset();
  for (const rel of [
    'backend/pathfinder/app.py',
    'frontend/package.json',
    'discovery-config/CLAUDE.md',
    'proto-config/CLAUDE.md',
    'rule/aiplc-rules/aws-aiplc-rules/core-workflow.md',
    'rule/aiplc-rules/language/ko.md',
    'rule/aiplc-rules/language/en.md',
  ]) {
    assert.ok(fs.existsSync(path.join(root, rel)),
      `deploy asset must still contain ${rel}`);
  }
  console.log('OK  asset: rules, both language directives and both config dirs still ship');
}

testAppAssetExcludesDevOnlyClaudeConfig();
testAppAssetStillShipsWhatTheRuntimeNeeds();
