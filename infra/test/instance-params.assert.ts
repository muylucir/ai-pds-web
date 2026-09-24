import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { INSTANCE_PARAMS } from '../lib/instance-params';
import { AipdsAgentCredsStack } from '../lib/aipds-agent-creds-stack';
import { AipdsPreviewStack } from '../lib/aipds-preview-stack';
import { AipdsUploadCorsStack } from '../lib/aipds-upload-cors-stack';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';

// 스택이 쓰는 파라미터 이름(lib/instance-params.ts)과 인스턴스가 읽는 이름(scripts/aipds-harden)이
// 같아야 한다. 어긋나면 스택 테스트도 스크립트도 각각 통과하고, 새 인스턴스는 "스택 미배포"로
// 읽어 격리를 끈 채 부팅한다 — 그 사실은 교체 뒤에야 드러난다.

const scripts = path.join(__dirname, '..', 'scripts');
const harden = fs.readFileSync(path.join(scripts, 'aipds-harden'), 'utf8');
const preview = fs.readFileSync(path.join(scripts, 'aipds-preview-configure'), 'utf8');

for (const [variable, name] of [
  ['PARAM_AGENT_ROLE', INSTANCE_PARAMS.agentRoleArn],
  ['PARAM_PREVIEW_ORIGIN', INSTANCE_PARAMS.previewOrigin],
  ['PARAM_PREVIEW_SECRET', INSTANCE_PARAMS.previewSecretArn],
]) {
  assert.ok(harden.includes(`\n${variable}=${name}\n`), `aipds-harden ${variable} must be ${name}`);
}

for (const file of ['aipds-harden', 'aipds-preview-configure']) {
  execFileSync('bash', ['-n', path.join(scripts, file)]);
}

// sync의 동작(값이 늦게 생겨도 반영, 못 읽으면 끄지 않음, hold, set -e 안전)은
// backend/tests/test_harden_script.py가 스크립트를 실제로 돌려 본다. 여기는 배선만 본다.
//
// boot는 서비스가 뜨기 **전에** 돈다(user-data) — 재시작하면 안 된다. 타이머의 sync는 바뀐 것이
// 있으면 재시작한다.
assert.match(harden, /\n  boot\)\n    install_all --no-restart\n    sync_apply 0\n/,
  'boot = install (no restart) + sync without restart');
assert.match(harden, /\n  sync\)\n[^\n]*\n    sync_apply 1\n/, 'sync may restart the backend');
// 타이머는 root로 돈다 — 리포 파일(aipds가 쓸 수 있다)이 아니라 root 소유 사본을 돌려야 한다.
// sync가 부르는 preview-configure도 마찬가지다.
assert.match(harden, /ExecStart=\$LIBEXEC\/harden sync/, 'the timer must run the root-owned copy');
assert.match(harden, /"\$LIBEXEC\/preview-configure" --no-restart/,
  'sync must call the root-owned preview-configure');
assert.ok(!/"\$APP\/infra\/scripts\/aipds-preview-configure" --no-restart/.test(harden),
  'sync must not run the repo copy of preview-configure as root');
for (const copy of ['harden', 'preview-configure']) {
  assert.ok(harden.includes(`"$LIBEXEC/${copy}"`), `install must place the ${copy} copy`);
}
const update = fs.readFileSync(path.join(scripts, 'aipds-update'), 'utf8');
assert.ok(update.includes('/usr/local/libexec/aipds/harden'),
  'aipds-update must refresh the harden copy the timer runs');
assert.match(harden, /OnUnitActiveSec=2min/, 'sync runs periodically');
assert.match(harden, /systemctl enable --now "\$SYNC_UNIT\.timer"/, 'install enables the timer');
assert.match(preview, /if \[ "\$\{1:-\}" = "--no-restart" \]; then/,
  'aipds-preview-configure must accept --no-restart');

// 두 스택이 같은 인스턴스 롤에 정책을 붙인다. 인라인 정책 이름은 construct 경로에서 나오므로
// 같으면 두 번째 배포가 "already managed by another stack"으로 실패한다(실제로 그랬다).
{
  const env = { account: '123456789012', region: 'ap-northeast-2' };
  const role = 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole';
  const app = new cdk.App();
  const stacks = [
    new AipdsAgentCredsStack(app, 'AgentCreds', { env, instanceRoleArn: role }),
    new AipdsPreviewStack(app, 'Preview', {
      env, instanceRoleArn: role, originDnsName: 'ec2-1-2-3-4.compute.amazonaws.com',
      originVerifySecretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:h-AbCdEf',
    }),
    new AipdsUploadCorsStack(app, 'UploadCors', {
      env, instanceRoleArn: role, bucketArn: 'arn:aws:s3:::aipdsdrillstack-artifacts-x',
    }),
  ];
  const names = stacks.flatMap((st) =>
    Object.values(Template.fromStack(st).findResources('AWS::IAM::Policy'))
      .map((p: any) => p.Properties.PolicyName));
  assert.strictEqual(new Set(names).size, names.length,
    `policies on the shared instance role must have distinct names: ${names}`);
}

// 버킷에 CORS가 없을 때 harden이 만드는 규칙은 DrillStack의 것과 같은 모양이어야 한다 — 다르면
// 둘 중 누가 마지막에 썼느냐에 따라 업로드 헤더 허용이 달라진다.
{
  const app = new cdk.App();
  const bucket = Object.values(Template.fromStack(new AipdsDrillStack(app, 'D', {
    env: { account: '123456789012', region: 'ap-northeast-2' } })).findResources('AWS::S3::Bucket'))[0] as any;
  const rule = bucket.Properties.CorsConfiguration.CorsRules[0];
  const mergeRule = harden.slice(harden.indexOf('CORS_MERGE='), harden.indexOf('sync_cors() {'));
  assert.ok(mergeRule.includes(`"AllowedMethods": ${JSON.stringify(rule.AllowedMethods)}`), 'methods');
  assert.ok(mergeRule.includes(`"AllowedHeaders": ${JSON.stringify(rule.AllowedHeaders)}`), 'headers');
  assert.ok(mergeRule.includes(`"ExposeHeaders": ${JSON.stringify(rule.ExposedHeaders)}`), 'exposed');
  assert.ok(mergeRule.includes(`"MaxAgeSeconds": ${rule.MaxAge}`), 'max age');
}

console.log('OK  instance params: stacks and aipds-harden agree; boot applies without restarting');
