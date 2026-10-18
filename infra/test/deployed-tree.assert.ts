// infra/test/deployed-tree.assert.ts
//
// Checks that **nothing that must not be in the tree that becomes /opt/aipds is there**.
//
// This test replaced app-asset.assert.ts. Previously the code shipped as a CDK asset
// zip, so we synthesized and inspected the staged directory. Now EC2 clones the
// repo (lib/deploy-source.ts), so what lands on the instance is exactly **the files git
// tracks** — which is why the subject of the check is `git ls-files`.
// No synth is needed, so it is much faster, and above all the check now matches the real deploy path.
//
// **Why this invariant exists (observed 2026-08-04).** Workspace chat in projects that chose English
// kept going in Korean. Half the cause was discovery-config (that side is guarded by
// backend/tests/test_agent_language.py), and the other half is this:
//
//   agent cwd           /opt/aipds/workspaces/{pid}
//   what was in tree    /opt/aipds/.claude/CLAUDE.md   <- **ancestor**
//
// Claude Code loads every CLAUDE.md walking up from cwd (confirmed with the real CLI:
// `claude --debug -p "로드한 CLAUDE.md 경로를 나열해라"` lists ancestor files marked `(ancestor
// project)`). So a single Korean line in the repo's dev .claude/CLAUDE.md entered every
// turn's context in English projects.
//
// **There is no switch to turn this off.** CLAUDE_CONFIG_DIR moves only the "user" level, and the
// ancestor search comes from the fact that "cwd is inside the app tree" (putting workspaces in the app
// tree is a placement user-data.ts intends for backups and permissions). So that file must not be in
// the tree, and this test pins that invariant.
//
// Where the clone approach is stronger than before: `.claude/` is in .gitignore and so is not tracked in
// the first place, so **it is impossible to forget to exclude it from the list.** In the asset days it shipped
// regardless of gitignore, so a separate exclusion list had to be maintained by hand, and something missing from that list caused the incident.
import * as assert from 'node:assert';
import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

const REPO_ROOT = path.join(__dirname, '..', '..');

/** The list of files that go on the instance = what git tracks. */
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

  // 1) The repo's dev .claude/ must not be tracked.
  const devClaude = files.filter((f) => f === '.claude' || f.startsWith('.claude/'));
  assert.deepStrictEqual(devClaude, [],
    'the repo-development .claude/ must NOT be tracked: it would be cloned to ' +
    '/opt/aipds/.claude/, an ANCESTOR of the agent cwd ' +
    '(/opt/aipds/workspaces/{pid}), and Claude Code loads ancestor ' +
    'CLAUDE.md files — measured against the real CLI. Its Korean line would ' +
    'then enter every English project\'s context, and there is no runtime ' +
    `switch to turn it off. Found: ${devClaude.join(', ')}`);

  // 2) There must be no unintended CLAUDE.md anywhere in the tree. The only intended ones are the two
  //    agent config dirs. If a new CLAUDE.md is committed to the repo, this assertion catches it.
  const ALLOWED = new Set([
    'discovery-config/CLAUDE.md',   // Discovery config dir (language-neutral)
    'proto-config/CLAUDE.md',       // prototype builder config dir
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
  // If a prototype build tree made on a dev box ships, a prototype nobody built
  // appears as "빌드 완료" (build complete) on a new instance —
  // this path is exactly what has_build_output in proto/session.py looks at.
  // Session transcripts and queues must not ship for the same reason.
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
  // Control group: guards against the regression of pushing something into .gitignore and leaving out
  // something needed at runtime. In the asset days the risk was the exclusion list growing too broad; now
  // .gitignore is in that place — a mistake in the same direction is still possible.
  const files = new Set(trackedFiles());
  for (const rel of [
    'backend/aipds/app.py',
    'frontend/package.json',
    'frontend/package-lock.json',   // npm ci requires it at boot
    'discovery-config/CLAUDE.md',
    'proto-config/CLAUDE.md',
    'proto-config/skills/shadcn-design/SKILL.md',
    // The rule set itself is now a submodule, not files —
    // testTheRulesetShipsAsASubmodule() checks it. Here we look only at the file that lets that submodule
    // be restored: without .gitmodules, `submodule update --init`
    // does nothing and silently succeeds.
    '.gitmodules',
    // The language directive moved out of the rule set tree on 2026-08-18 — upstream aiplc-rules/ has
    // no language/, so when the rule set is swapped out wholesale our content must not
    // disappear with it.
    //
    // **Then on 2026-08-19 (8b58cba) it moved again, from files to code** —
    // the two files `language/{ko,en}.md` became the `workspace_rules.LANGUAGE_DIRECTIVES`
    // constant. This list did not follow and kept requiring the deleted files,
    // and from that day this assertion failed (no runtime impact — boot does not need
    // those files). `test_the_language_directive_is_code_not_a_file` in
    // backend/tests/test_workspace_rules.py asserts the exact opposite, so
    // the two tests contradicted each other.
    //
    // The invariant is unchanged: **the language directive ships to the instance.** Only where it ships
    // changed, from files to that module.
    'backend/aipds/agent/workspace_rules.py',
  ]) {
    assert.ok(files.has(rel),
      `${rel} must be tracked — the instance clones the repo, so anything ` +
      'untracked simply does not exist at /opt/aipds');
  }
  console.log('OK  deployed tree: rules, the language directives, both config dirs and the lockfile ship');
}

function testTheRulesetShipsAsASubmodule() {
  // **Why a separate test.** The check in the list above is "is it tracked", and for submodules that is
  // not enough. A gitlink is tracked yet `git clone` does not fetch its contents, so checking only
  // tracked status would let an empty directory onto the instance.
  //
  // Why the rule set is a submodule (2026-08-21): previously the upstream aiplc-rules/ was copied into
  // the repo, and three files actually diverged (c806343 fixed a model ID). Upstream is the source of
  // truth, so that divergence is a defect on our side — without a copy, nothing can diverge.
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

  // Prevents the copy from coming back. Until 2026-08-21 this path was where the rule set lived,
  // and the shape of the reverting mistake is "keeping steering-files/ and recreating rule/ too".
  const revived = trackedFiles().filter((f) => f.startsWith('rule/'));
  assert.deepStrictEqual(revived, [],
    'the AI-PLC ruleset must live only in the steering-files/ submodule; a second copy '
    + `under rule/ is the drift this replaced. Found: ${revived.join(', ')}`);

  // So far only the index was checked. Finally, check that the working tree has contents — the
  // common case where this assertion fails is a fresh clone without the submodule initialized,
  // and that state is exactly the same as booting with no rules.
  assert.ok(fs.existsSync(path.join(
    REPO_ROOT, 'steering-files', 'aiplc-rules', 'aws-aiplc-rules', 'core-workflow.md')),
    'steering-files/ is empty — run: git submodule update --init --recursive');
  console.log('OK  deployed tree: the AI-PLC ruleset is an unmodified upstream submodule');
}

testNoUnexpectedClaudeMdInTheDeployedTree();
testNoBuiltPrototypesInTheDeployedTree();
testDeployedTreeStillShipsWhatTheRuntimeNeeds();
testTheRulesetShipsAsASubmodule();
