import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { bedrockSubscribeStatement, INVOKABLE_MODEL_ARNS } from './backend-permissions';
import { INSTANCE_PARAMS } from './instance-params';

// Role for the credentials received by sandboxed processes (Discovery and build agents, hosted
// prototypes).
//
// **Why not just hand over the instance role.** That role has read and delete over the whole
// bucket, the Cognito Admin API, and read access to the header secret. The agent runs exposed to
// prompt injection from uploaded documents, and prototypes run with code the agent wrote and the
// npm supply chain. Those processes cannot reach IMDS under the launch wrapper
// (infra/scripts/aipds-launch); the backend AssumeRoles this role and hands it over via a loopback
// endpoint (backend/aipds/credentials.py). All they can do is call Bedrock (including the
// Marketplace auto-subscription on the first call).
//
// **Why a separate stack.** Deploying HostingStack can replace the EC2 instance (the same situation
// as lib/aipds-preview-stack.ts). This stack only references the instance role by ARN. The
// AssumeRole permission is a separate policy (AWS::IAM::Policy) attached to the instance role, so
// the HostingStack template stays as is — we do not rely on whether the trust policy alone is
// enough (same account).
//
// **How the instance learns about this role.** The ARN is written to an SSM parameter
// (lib/instance-params.ts). A new instance reads it at boot, turns on the wrapper, and blocks IMDS
// — so there is no window in which a replaced instance runs without isolation while waiting for an
// operator.

export interface AgentCredsStackProps extends cdk.StackProps {
  /** HostingStack's instance role. This role AssumeRoles AgentRole. */
  instanceRoleArn: string;
}

export class AipdsAgentCredsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCredsStackProps) {
    super(scope, id, props);

    const agentRole = new iam.Role(this, 'AgentRole', {
      description: 'AI-PDS sandboxed agents and prototypes: Bedrock invoke only.',
      assumedBy: new iam.ArnPrincipal(props.instanceRoleArn),
      // Instance role → this role is role chaining, so the session is capped at 1 hour anyway.
      maxSessionDuration: cdk.Duration.hours(1),
    });
    agentRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODEL_ARNS(this.account),
    }));
    // Marketplace auto-subscription on the first call (lib/backend-permissions.ts).
    agentRole.addToPolicy(bedrockSubscribeStatement());

    const instanceRole = iam.Role.fromRoleArn(this, 'InstanceRole', props.instanceRoleArn, {
      mutable: true,
    });
    agentRole.grantAssumeRole(instanceRole);
    // The instance role also calls Bedrock directly (questionnaire generation, etc.). HostingStack
    // already grants it via backendPolicyStatements, but in environments that do not redeploy that
    // stack (pinned mode, lib/sandbox-stacks.ts), the instance role has no other way to get it
    // except through this stack. Attaching the same statement twice yields the same permissions.
    instanceRole.addToPrincipalPolicy(bedrockSubscribeStatement());

    const param = new ssm.StringParameter(this, 'AgentRoleArnParam', {
      parameterName: INSTANCE_PARAMS.agentRoleArn,
      stringValue: agentRole.roleArn,
      description: 'AI-PDS AgentRole; read at boot by infra/scripts/aipds-harden.',
    });
    param.grantRead(instanceRole);

    new cdk.CfnOutput(this, 'AgentRoleArn', { value: agentRole.roleArn });
  }
}
