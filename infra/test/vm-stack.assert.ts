import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderVmStack } from '../lib/pathfinder-vm-stack';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-1' };

// infra/build/harness is gitignored — package-harness.sh (which needs the
// gitignored files/aiplc-rules/ tree) produces it on a real dev/drill
// machine. In CI there's no such tree, so s3assets.Asset would fail synth.
// Stub the minimal dir here so PathfinderVmStack can synth without it; a
// bare Dockerfile file is enough content for the Asset construct.
function ensureStubHarnessBuildDir() {
  const dir = path.join(__dirname, '..', 'build', 'harness');
  if (!fs.existsSync(path.join(dir, 'Dockerfile'))) {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'Dockerfile'), 'FROM scratch\n');
  }
}

ensureStubHarnessBuildDir();

function makeVmStack() {
  const app = new cdk.App();
  const stack = new PathfinderVmStack(app, 'Vm', { env: ENV });
  return Template.fromStack(stack);
}

function testImage() {
  const t = makeVmStack();
  t.hasResourceProperties('AWS::Lambda::MicrovmImage', {
    // 'prototype' 네임스페이스: Tokyo에 남은 이전 drill 스택이
    // 'pathfinder-harness' 이미지와 /pathfinder/microvm/harness 로그 그룹을
    // 아직 소유하므로, 같은 이름이면 "already exists"로 배포가 막힌다.
    Name: 'pathfinder-prototype-harness',
    BaseImageVersion: '1',
    CpuConfigurations: Match.arrayWith([
      Match.objectLike({ Architecture: 'ARM_64' }),
    ]),
    Resources: Match.arrayWith([
      Match.objectLike({ MinimumMemoryInMiB: 2048 }),
    ]),
    Hooks: Match.objectLike({
      Port: 9000,
      MicrovmImageHooks: Match.objectLike({
        Ready: 'ENABLED',
        ReadyTimeoutInSeconds: 300,
        Validate: 'ENABLED',
        ValidateTimeoutInSeconds: 60,
      }),
    }),
  });
  t.resourceCountIs('AWS::Lambda::MicrovmImage', 1);
  console.log('OK  vm-stack: image hooks ENABLED port 9000 + ARM_64 + baseImageVersion 1');
}

testImage();

function testEnvVars() {
  const t = makeVmStack();
  const images = t.findResources('AWS::Lambda::MicrovmImage');
  const image = Object.values(images)[0] as any;
  const envVars = image.Properties.EnvironmentVariables as Array<{ Key: string; Value: string }>;
  assert.strictEqual(envVars.length, 2, 'exactly 2 env vars — no PATHFINDER_DRIVER');
  const byKey = Object.fromEntries(envVars.map((e) => [e.Key, e.Value]));
  assert.strictEqual(byKey['CLAUDE_CODE_USE_BEDROCK'], '1');
  assert.strictEqual(byKey['ANTHROPIC_MODEL'], 'global.anthropic.claude-opus-4-8');
  assert.ok(!('PATHFINDER_DRIVER' in byKey), 'PATHFINDER_DRIVER must be deleted');
  console.log('OK  vm-stack: exactly 2 env vars (CLAUDE_CODE_USE_BEDROCK, ANTHROPIC_MODEL), no PATHFINDER_DRIVER');
}

testEnvVars();

function testExecutionRoleBedrockOnlyNoS3() {
  const t = makeVmStack();
  const policies = t.findResources('AWS::IAM::Policy');
  // Find the policy attached to the ExecutionRole logical id.
  const roles = t.findResources('AWS::IAM::Role');
  const execRoleLogicalId = Object.keys(roles).find((id) => id.startsWith('ExecutionRole'));
  assert.ok(execRoleLogicalId, 'ExecutionRole must exist');

  const execPolicies = Object.entries(policies).filter(([, r]) => {
    const roleRefs = (r as any).Properties.Roles ?? [];
    return roleRefs.some((ref: any) => ref?.Ref === execRoleLogicalId);
  });
  assert.ok(execPolicies.length >= 1, 'ExecutionRole must have at least one policy');

  const execPolicyJson = JSON.stringify(execPolicies);
  assert.match(execPolicyJson, /bedrock:InvokeModel/, 'exec role must invoke bedrock');
  assert.match(execPolicyJson, /bedrock:InvokeModelWithResponseStream/, 'exec role must invoke bedrock stream');
  assert.match(execPolicyJson, /inference-profile\/global\.anthropic\.claude-opus-4-8/, 'exec role bedrock stmt must have inference-profile ARN shape');
  assert.match(execPolicyJson, /foundation-model\/anthropic\.claude-opus-4-8/, 'exec role bedrock stmt must have foundation-model ARN shape');
  // No s3:* action anywhere in the exec role's policies.
  assert.doesNotMatch(execPolicyJson, /"s3:/, 'ExecutionRole must have NO s3:* statements');
  console.log('OK  vm-stack: ExecutionRole has Bedrock statement (both ARN shapes), no s3:* statements');
}

testExecutionRoleBedrockOnlyNoS3();

function testBuildRoleSourceArnCondition() {
  const t = makeVmStack();
  t.hasResourceProperties('AWS::IAM::Role', {
    AssumeRolePolicyDocument: Match.objectLike({
      Statement: Match.arrayWith([
        Match.objectLike({
          Principal: Match.objectLike({ Service: 'lambda.amazonaws.com' }),
          Condition: Match.objectLike({
            StringEquals: Match.objectLike({ 'aws:SourceAccount': ENV.account }),
            ArnLike: Match.objectLike({
              'aws:SourceArn': Match.stringLikeRegexp('microvm-image'),
            }),
          }),
        }),
      ]),
    }),
  });
  console.log('OK  vm-stack: BuildRole/ExecutionRole trust condition has SourceAccount + SourceArn(microvm-image:*)');
}

testBuildRoleSourceArnCondition();

function testNoArtifactsBucketInVmStack() {
  const t = makeVmStack();
  t.resourceCountIs('AWS::S3::Bucket', 0);
  console.log('OK  vm-stack: no S3 bucket (Seoul drill stack owns artifacts)');
}

testNoArtifactsBucketInVmStack();

function testOutputs() {
  const t = makeVmStack();
  const outputs = t.toJSON().Outputs;
  assert.ok(outputs.ImageArn, 'ImageArn output must exist');
  assert.ok(outputs.ExecutionRoleArn, 'ExecutionRoleArn output must exist');
  assert.ok(outputs.Region, 'Region output must exist');
  console.log('OK  vm-stack: outputs ImageArn, ExecutionRoleArn, Region present');
}

testOutputs();

// --- 백엔드/호스팅 롤에 microvm 제어 statement가 실렸는지 ---

function testDrillBackendRoleHasMicrovmControl() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'DrillMv', { env: { account: '123456789012', region: 'ap-northeast-2' } });
  const t = Template.fromStack(drill);
  const policies = t.findResources('AWS::IAM::Policy');
  const json = JSON.stringify(policies);
  assert.match(json, /lambda:RunMicrovm/, 'drill backend role must have lambda:RunMicrovm');
  assert.match(json, /lambda:GetMicrovm/, 'drill backend role must have lambda:GetMicrovm');
  assert.match(json, /lambda:TerminateMicrovm/, 'drill backend role must have lambda:TerminateMicrovm');
  assert.match(json, /lambda:ListMicrovms/, 'drill backend role must have lambda:ListMicrovms');
  assert.match(json, /lambda:CreateMicrovmAuthToken/, 'drill backend role must have lambda:CreateMicrovmAuthToken');
  assert.match(json, /microvm:\*/, 'must scope to microvm:* resource');
  assert.match(json, /microvm-image:\*/, 'must scope to microvm-image:* resource (RunMicrovm needs the image)');
  console.log('OK  drill stack: backend role carries microvm control statements (Tokyo-scoped)');
}

testDrillBackendRoleHasMicrovmControl();

function testHostingInstanceRoleHasMicrovmControl() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'DrillMv2', { env: { account: '123456789012', region: 'ap-northeast-2' } });
  const hosting = new PathfinderHostingStack(app, 'HostingMv', {
    env: { account: '123456789012', region: 'ap-northeast-2' },
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',
  });
  const t = Template.fromStack(hosting);
  const policies = t.findResources('AWS::IAM::Policy');
  const json = JSON.stringify(policies);
  assert.match(json, /lambda:RunMicrovm/, 'hosting instance role must have lambda:RunMicrovm');
  assert.match(json, /lambda:CreateMicrovmAuthToken/, 'hosting instance role must have lambda:CreateMicrovmAuthToken');
  console.log('OK  hosting stack: instance role carries microvm control statements (Tokyo-scoped)');
}

testHostingInstanceRoleHasMicrovmControl();
