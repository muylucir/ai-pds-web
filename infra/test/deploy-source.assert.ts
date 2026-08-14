// infra/test/deploy-source.assert.ts
//
// 배포 대상(리포 + 브랜치)을 정하는 상수. 여기가 틀리면 실패가 **배포가 아니라
// 부팅에서** 난다 — clone은 EC2가 부팅할 때 하므로, cdk deploy는 성공하고
// 인스턴스만 502가 된다.
//
// 종전에는 이 파일이 커밋 SHA 결정 로직(로컬 HEAD 폴백, CDK_DEPLOY_REF, 푸시
// 여부 판정)을 검증했다. 그 로직이 사라진 이유는 lib/deploy-source.ts에 있다.
// 남은 위험은 하나다: **여기에 커밋 SHA가 들어오는 것.** 그러면 배포가 다시
// 고정되고, pathfinder-update가 갱신할 것이 없어지는데 아무 에러도 나지 않는다.
import * as assert from 'node:assert';

import { DEPLOY_BRANCH, REPO_URL } from '../lib/deploy-source';

function testRepoUrlIsPublicHttps() {
  // HTTPS여야 인스턴스에 자격증명이 필요 없다. SSH(git@)로 바꾸면 부팅 시
  // clone이 인증을 요구하며 실패하고, 증상은 502뿐이다.
  assert.ok(REPO_URL.startsWith('https://'),
    'clone URL must be HTTPS — the instance has no git credentials');
  assert.ok(REPO_URL.endsWith('.git'), 'clone URL should be a git URL');
  console.log(`OK  deploy-source: public https clone url (${REPO_URL})`);
}

function testDeployTargetIsABranchNotACommit() {
  assert.ok(DEPLOY_BRANCH.length > 0, 'a deploy branch must be set');
  // SHA를 넣으면 인스턴스가 그 커밋에 고정되고, 최신 코드를 당기는 경로
  // (pathfinder-update와 부팅 시 checkout)가 조용히 무력화된다.
  assert.doesNotMatch(DEPLOY_BRANCH, /^[0-9a-f]{7,40}$/,
    'the deploy target must be a branch name, not a commit SHA — a SHA pins the '
    + 'instance and makes pathfinder-update a no-op, with no error to show it');
  // user-data 문자열과 `git checkout -B <branch> origin/<branch>`에 그대로
  // 들어가는 값이다. 공백·refs/ 접두어·와일드카드는 부팅에서만 실패한다.
  assert.doesNotMatch(DEPLOY_BRANCH, /\s/, 'branch name must not contain whitespace');
  assert.doesNotMatch(DEPLOY_BRANCH, /^refs\//,
    'use the short branch name — it is used as both the local branch and origin/<branch>');
  console.log(`OK  deploy-source: deploy target is a branch (${DEPLOY_BRANCH}), not a pinned commit`);
}

testRepoUrlIsPublicHttps();
testDeployTargetIsABranchNotACommit();
