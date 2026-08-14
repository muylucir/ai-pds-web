// infra/test/deploy-source.assert.ts
//
// 배포 커밋 결정 로직. 여기가 틀리면 실패가 **배포가 아니라 부팅에서** 난다 —
// clone은 EC2가 부팅할 때 하므로, cdk deploy는 성공하고 인스턴스만 502가 된다.
// 그래서 "조용히 그럴듯한 값으로 떨어지는" 경로가 하나도 없어야 한다.
import * as assert from 'node:assert';
import { execFileSync } from 'node:child_process';

import { DEPLOY_REF_ENV, REPO_URL, resolveDeployRef, warnIfRefNotPushed } from '../lib/deploy-source';

function withEnv(value: string | undefined, fn: () => void) {
  const saved = process.env[DEPLOY_REF_ENV];
  if (value === undefined) delete process.env[DEPLOY_REF_ENV];
  else process.env[DEPLOY_REF_ENV] = value;
  try {
    fn();
  } finally {
    if (saved === undefined) delete process.env[DEPLOY_REF_ENV];
    else process.env[DEPLOY_REF_ENV] = saved;
  }
}

function testRepoUrlIsPublicHttps() {
  // HTTPS여야 인스턴스에 자격증명이 필요 없다. SSH(git@)로 바꾸면 부팅 시
  // clone이 인증을 요구하며 실패하고, 증상은 502뿐이다.
  assert.ok(REPO_URL.startsWith('https://'),
    'clone URL must be HTTPS — the instance has no git credentials');
  assert.ok(REPO_URL.endsWith('.git'), 'clone URL should be a git URL');
  console.log(`OK  deploy-source: public https clone url (${REPO_URL})`);
}

function testExplicitRefWins() {
  withEnv('  deadbeef  ', () => {
    // 공백은 다듬는다 — 셸에서 복사·붙여넣기한 값이 그대로 user-data에 들어가면
    // `git checkout --detach ' deadbeef '`가 된다.
    assert.strictEqual(resolveDeployRef(), 'deadbeef');
  });
  console.log('OK  deploy-source: CDK_DEPLOY_REF wins and is trimmed');
}

function testFallsBackToLocalHead() {
  withEnv(undefined, () => {
    const ref = resolveDeployRef();
    const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    assert.strictEqual(ref, head);
    // 브랜치 이름으로 떨어지면 안 된다: 값이 바뀌지 않으면 user-data가 그대로이고,
    // 그러면 CloudFormation이 인스턴스를 교체하지 않아 배포가 코드를 갱신하지 못한다.
    assert.match(ref, /^[0-9a-f]{40}$/,
      'the fallback must be a full commit SHA, never a branch name — a branch name '
      + 'would keep user-data byte-identical across commits and stop the instance from being replaced');
  });
  console.log('OK  deploy-source: falls back to the local HEAD sha (not a branch name)');
}

function testEmptyEnvIsTreatedAsUnset() {
  // `CDK_DEPLOY_REF= npx cdk deploy` 는 빈 문자열을 준다. 그것을 그대로 쓰면
  // `git checkout --detach` 가 되어 부팅이 깨진다.
  withEnv('', () => {
    assert.match(resolveDeployRef(), /^[0-9a-f]{40}$/);
  });
  withEnv('   ', () => {
    assert.match(resolveDeployRef(), /^[0-9a-f]{40}$/);
  });
  console.log('OK  deploy-source: empty/whitespace CDK_DEPLOY_REF falls back instead of shipping an empty ref');
}

function testWarnsOnRefThatIsNotACommit() {
  // 이 경로가 이 파일이 존재하는 주된 이유다. 종전 구현은 git의 에러를 삼켜
  // 오타가 아무 경고 없이 배포됐다 — 그리고 그 실패는 부팅에서만 보인다.
  const w = warnIfRefNotPushed('0000000000000000000000000000000000000000');
  assert.ok(w, 'a sha that is not a commit in this repo must warn');
  assert.match(w!, /not a commit in this repository/);
  assert.match(w!, new RegExp(DEPLOY_REF_ENV), 'the warning should name the env var to check');

  const typo = warnIfRefNotPushed('mian');   // "main" 오타
  assert.ok(typo, 'an unknown ref name must warn');
  console.log('OK  deploy-source: warns when the ref is not a commit here (typo / not fetched)');
}

function testDoesNotWarnForAPushedCommit() {
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const onRemote = execFileSync('git', ['branch', '--remotes', '--contains', head], { encoding: 'utf8' }).trim();
  if (!onRemote) {
    // HEAD가 아직 푸시되지 않은 상태에서 이 테스트가 도는 경우 — 그때는
    // 반대 방향을 확인한다(경고가 나와야 한다).
    const w = warnIfRefNotPushed(head);
    assert.ok(w, 'HEAD is not on a remote branch here, so it must warn');
    assert.match(w!, /not on any remote branch/);
    console.log('OK  deploy-source: warns for the local-but-unpushed HEAD (this checkout is ahead of origin)');
    return;
  }
  assert.strictEqual(warnIfRefNotPushed(head), null,
    'a commit that is on a remote branch must not warn');
  console.log('OK  deploy-source: silent for a pushed commit');
}

testRepoUrlIsPublicHttps();
testExplicitRefWins();
testFallsBackToLocalHead();
testEmptyEnvIsTreatedAsUnset();
testWarnsOnRefThatIsNotACommit();
testDoesNotWarnForAPushedCommit();
