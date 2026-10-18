// infra/test/deploy-source.assert.ts
//
// Constants that determine the deploy target (repo + branch). If these are wrong, the failure happens
// **at boot, not at deploy** — the clone happens when EC2 boots, so cdk deploy succeeds and
// only the instance returns 502.
//
// Previously this file verified the commit-SHA resolution logic (local HEAD fallback, CDK_DEPLOY_REF,
// whether it was pushed). Why that logic went away is in lib/deploy-source.ts.
// One risk remains: **a commit SHA getting in here.** Then deploys become pinned again,
// and aipds-update has nothing to update, yet no error appears.
import * as assert from 'node:assert';

import { DEPLOY_BRANCH, REPO_URL } from '../lib/deploy-source';

function testRepoUrlIsPublicHttps() {
  // HTTPS means the instance needs no credentials. Switching to SSH (git@) makes the boot-time
  // clone demand authentication and fail, and the only symptom is a 502.
  assert.ok(REPO_URL.startsWith('https://'),
    'clone URL must be HTTPS — the instance has no git credentials');
  assert.ok(REPO_URL.endsWith('.git'), 'clone URL should be a git URL');
  console.log(`OK  deploy-source: public https clone url (${REPO_URL})`);
}

function testDeployTargetIsABranchNotACommit() {
  assert.ok(DEPLOY_BRANCH.length > 0, 'a deploy branch must be set');
  // Putting in a SHA pins the instance to that commit, and the paths that pull the latest code
  // (aipds-update and the boot-time checkout) are silently neutralized.
  assert.doesNotMatch(DEPLOY_BRANCH, /^[0-9a-f]{7,40}$/,
    'the deploy target must be a branch name, not a commit SHA — a SHA pins the '
    + 'instance and makes aipds-update a no-op, with no error to show it');
  // This value goes verbatim into the user-data string and `git checkout -B <branch> origin/<branch>`.
  // Whitespace, a refs/ prefix, or wildcards fail only at boot.
  assert.doesNotMatch(DEPLOY_BRANCH, /\s/, 'branch name must not contain whitespace');
  assert.doesNotMatch(DEPLOY_BRANCH, /^refs\//,
    'use the short branch name — it is used as both the local branch and origin/<branch>');
  console.log(`OK  deploy-source: deploy target is a branch (${DEPLOY_BRANCH}), not a pinned commit`);
}

testRepoUrlIsPublicHttps();
testDeployTargetIsABranchNotACommit();
