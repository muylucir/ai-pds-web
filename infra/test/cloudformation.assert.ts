import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { spawnSync } from 'node:child_process';
import YAML from 'yaml';

const templatePath = path.join(__dirname, '..', 'cloudformation', 'pathfinder.yaml');
const template = YAML.parse(fs.readFileSync(templatePath, 'utf8')) as any;
const resources = template.Resources as Record<string, any>;
const parameters = template.Parameters as Record<string, any>;
const outputs = template.Outputs as Record<string, any>;

assert.strictEqual(template.AWSTemplateFormatVersion, '2010-09-09');
assert.ok(parameters.AppAssetBucket, 'template must accept the pre-uploaded application ZIP bucket');
assert.ok(parameters.AppAssetKey, 'template must accept a content-addressed application ZIP key');
assert.strictEqual(parameters.SeedPassword.NoEcho, true, 'seed password must be hidden as a parameter');
assert.ok(!Object.values(resources).some((resource: any) => resource.Type === 'AWS::CloudFormation::Stack'),
  'the deployment must be one stack, not nested templates');

const count = (type: string) => Object.values(resources).filter((resource: any) => resource.Type === type).length;
assert.strictEqual(count('AWS::S3::Bucket'), 1);
assert.strictEqual(count('AWS::Cognito::UserPool'), 1);
assert.strictEqual(count('AWS::Cognito::UserPoolClient'), 1);
assert.strictEqual(count('AWS::Cognito::UserPoolGroup'), 2);
assert.strictEqual(count('AWS::CloudFront::Distribution'), 1);
assert.strictEqual(count('AWS::EC2::VPC'), 1);
assert.strictEqual(count('AWS::EC2::Subnet'), 2);
assert.strictEqual(count('AWS::EC2::NatGateway'), 0);
assert.strictEqual(count('AWS::EC2::LaunchTemplate'), 1);
assert.strictEqual(count('AWS::AutoScaling::AutoScalingGroup'), 1);

const client = resources.UserPoolClient.Properties;
assert.deepStrictEqual(client.AllowedOAuthFlows, ['code']);
assert.strictEqual(client.GenerateSecret, true);
assert.ok(client.CallbackURLs.some((value: any) => value['Fn::Sub'] === 'https://${Distribution.DomainName}/api/auth/callback'),
  'Cognito callback must directly reference the same-stack CloudFront distribution');
assert.ok(!client.AllowedOAuthFlows.includes('implicit'), 'implicit OAuth must remain disabled');

assert.strictEqual(resources.UserPool.Properties.AdminCreateUserConfig.AllowAdminCreateUserOnly, true);
assert.deepStrictEqual(resources.UserPool.Properties.AliasAttributes, ['email']);
assert.strictEqual(resources.ManagedLoginBranding.Properties.UseCognitoProvidedValues, true);
assert.strictEqual(resources.SeedUsers.Type, 'Custom::PathfinderSeedUsers');

const ingress = resources.CloudFrontIngress.Properties;
assert.ok(ingress.SourcePrefixListId['Fn::GetAtt'], 'port 80 source must be the looked-up CloudFront prefix list');
assert.strictEqual(ingress.FromPort, 80);
assert.strictEqual(ingress.ToPort, 80);
assert.ok(!JSON.stringify(resources.InstanceSecurityGroup).includes('SecurityGroupIngress'),
  'security group must not contain an open inline ingress rule');

const distribution = resources.Distribution.Properties.DistributionConfig;
const origin = distribution.Origins[0];
assert.strictEqual(origin.CustomOriginConfig.OriginProtocolPolicy, 'http-only');
assert.strictEqual(origin.OriginCustomHeaders[0].HeaderName, 'X-Origin-Verify');
assert.ok(JSON.stringify(origin.OriginCustomHeaders[0].HeaderValue).includes('secretsmanager'),
  'origin header value must be a Secrets Manager dynamic reference');
assert.strictEqual(distribution.DefaultCacheBehavior.ViewerProtocolPolicy, 'redirect-to-https');

const launchTemplate = resources.AppLaunchTemplate.Properties.LaunchTemplateData;
assert.strictEqual(launchTemplate.MetadataOptions.HttpTokens, 'required');
assert.strictEqual(launchTemplate.BlockDeviceMappings[0].Ebs.VolumeSize, 100);
const userData = launchTemplate.UserData['Fn::Base64']['Fn::Sub'];
assert.ok(userData.includes("s3://${AppAssetBucket}/${AppAssetKey}"), 'instance must download the parameterized app ZIP');
assert.ok(userData.includes('User=pathfinder'), 'application and build agents must run non-root');
assert.ok(userData.includes('NEXT_PUBLIC_API_BASE_URL=/api'), 'frontend must use the same-origin API proxy');
assert.ok(userData.includes('PATHFINDER_COGNITO_USER_POOL_ID=${UserPool}'), 'backend authentication must be enabled');
assert.ok(userData.includes('APP_BASE_URL=https://${Distribution.DomainName}'), 'frontend callback base must match CloudFront');

const asg = resources.AppAutoScalingGroup;
assert.strictEqual(asg.Properties.MinSize, '1');
assert.strictEqual(asg.Properties.MaxSize, '1');
assert.ok(asg.Properties.LaunchTemplate.Version['Fn::GetAtt'].includes('LatestVersionNumber'));
assert.strictEqual(asg.UpdatePolicy.AutoScalingRollingUpdate.MaxBatchSize, 1,
  'a new content-addressed asset key must roll the one-instance ASG');
assert.strictEqual(resources.EipAttachment.Type, 'Custom::PathfinderEipAttachment');
assert.ok(resources.EipAttachment.Properties.LaunchTemplateVersion,
  'EIP attachment custom resource must rerun after an application rollout');

const policies = JSON.stringify(resources);
for (const prefix of ['projects/*', 'sessions/*', 'surveys/*', 'models/*']) {
  assert.ok(policies.includes(prefix), `runtime S3 policies must cover ${prefix}`);
}
for (const action of [
  'bedrock:InvokeModelWithResponseStream',
  'cognito-idp:DescribeUserPoolClient',
  'cognito-idp:AdminCreateUser',
  'secretsmanager:GetSecretValue',
]) {
  assert.ok(policies.includes(action), `required IAM action missing: ${action}`);
}
assert.ok(policies.includes('arn:${AWS::Partition}:s3:::${AppAssetBucket}/${AppAssetKey}'),
  'instance role must read exactly the supplied app asset');
assert.strictEqual(resources.ArtifactsBucketCleanup.Type, 'Custom::PathfinderEmptyBucket',
  'runtime bucket must be emptied before stack deletion');

for (const name of [
  'DistributionDomain', 'InstanceId', 'EipAddress', 'ArtifactsBucketName',
  'BackendRoleArn', 'Region', 'UserPoolId', 'UserPoolClientId', 'HostedUiDomain',
]) {
  assert.ok(outputs[name], `missing output: ${name}`);
}
assert.ok(!outputs.ClientSecret, 'Cognito client secret must never be a stack output');

const providerCode = resources.CustomProviderFunction.Properties.Code.ZipFile as string;
for (const mode of ['PrefixList', 'SeedUsers', 'EmptyBucket', 'AttachEip']) {
  assert.ok(providerCode.includes(`mode == '${mode}'`), `inline provider must implement ${mode}`);
}
const pythonCheck = spawnSync('python3', ['-c', 'import sys; compile(sys.stdin.read(), "inline-provider.py", "exec")'], {
  input: providerCode,
  encoding: 'utf8',
});
assert.strictEqual(pythonCheck.status, 0, `inline provider Python syntax error:\n${pythonCheck.stderr}`);

console.log('OK  single CloudFormation YAML: one stack, CDK-equivalent auth/storage/network/compute, secure origin, rolling app replacement');
