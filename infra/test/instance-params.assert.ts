import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { INSTANCE_PARAMS } from '../lib/instance-params';
import { AipdsAgentCredsStack } from '../lib/aipds-agent-creds-stack';
import { AipdsPreviewStack } from '../lib/aipds-preview-stack';

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

// boot는 서비스가 뜨기 **전에** 돈다(user-data) — 재시작하면 아직 없는 unit을 띄우거나, 부팅 순서를
// 어긴다. 그리고 격리를 켤 때는 둘 다 켠다: 래퍼 없이 IMDS만 막으면 아무것도 막지 않는다
// (IPAddressDeny는 래퍼가 띄운 unit에만 걸린다).
const bootApply = harden.slice(harden.indexOf('boot_apply() {'), harden.indexOf('install_all() {'));
assert.ok(bootApply.length > 0, 'aipds-harden must define boot_apply before install_all');
assert.ok(!/restart_backend|systemctl restart/.test(bootApply), 'boot must not restart services');
assert.match(bootApply, /write_launcher "\$arn"\n\s*write_imds 1/,
  'boot must turn the launcher on and block IMDS together');
assert.match(bootApply, /aipds-preview-configure" --no-restart/,
  'boot must configure the preview origin without restarting');
assert.match(harden, /\n  boot\)\n    install_all --no-restart\n    boot_apply\n/,
  'boot = install (no restart) + boot_apply');
// 프리뷰 값이 잘못돼도 부팅이 멈추면 안 된다 — user-data는 set -e이고, 멈추면 앱 전체가 502다.
assert.match(bootApply, /--no-restart "\$origin" "\$secret" \\\n\s*\|\| echo/,
  'a rejected preview parameter must not abort the boot');
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
  ];
  const names = stacks.flatMap((st) =>
    Object.values(Template.fromStack(st).findResources('AWS::IAM::Policy'))
      .map((p: any) => p.Properties.PolicyName));
  assert.strictEqual(new Set(names).size, names.length,
    `policies on the shared instance role must have distinct names: ${names}`);
}

console.log('OK  instance params: stacks and aipds-harden agree; boot applies without restarting');
