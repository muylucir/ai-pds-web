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

// The parameter names the stacks write (lib/instance-params.ts) and the names the instance reads (scripts/aipds-harden) must
// match. If they diverge, the stack tests and the script each still pass, and a new instance reads it as "stack not deployed"
// and boots with isolation off — which only comes to light after a replacement.

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

// sync's behavior (applying values that appear late, not turning off when unreadable, hold, set -e safety) is
// exercised by backend/tests/test_harden_script.py running the script for real. This file only checks the wiring.
//
// boot runs **before** the services start (user-data) — it must not restart them. The timer's sync restarts
// when something changed.
assert.match(harden, /\n  boot\)\n    install_all --no-restart\n    sync_apply 0\n/,
  'boot = install (no restart) + sync without restart');
assert.match(harden, /\n  sync\)\n[^\n]*\n    sync_apply 1\n/, 'sync may restart the backend');
// The timer runs as root — it must run the root-owned copy, not the repo file (which aipds can write).
// The same goes for preview-configure, which sync calls.
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

// Two stacks attach policies to the same instance role. Inline policy names come from the construct path,
// so if they are the same, the second deploy fails with "already managed by another stack" (it actually did).
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

// The rule harden creates when the bucket has no CORS must have the same shape as DrillStack's — if they differ,
// which upload headers are allowed depends on which of the two wrote last.
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
