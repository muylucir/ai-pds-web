import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

function testDrillUnchanged() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill', { env: ENV });
  const t = Template.fromStack(drill);

  // Bedrock invoke 문 존재.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']),
        }),
      ]),
    },
  });
  // 버킷 1개 노출.
  assert.ok(drill.artifactsBucket, 'artifactsBucket must be exposed');
  t.resourceCountIs('AWS::S3::Bucket', 1);
  console.log('OK  drill stack: policy + bucket exposed');
}

testDrillUnchanged();
