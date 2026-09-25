import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { bedrockSubscribeStatement, INVOKABLE_MODEL_ARNS } from './backend-permissions';
import { INSTANCE_PARAMS } from './instance-params';

// 샌드박스 프로세스(Discovery·빌드 에이전트, 호스팅된 프로토타입)가 받는 자격증명의 롤.
//
// **왜 인스턴스 롤을 그대로 주지 않는가.** 그 롤은 버킷 전체 읽기·삭제, Cognito Admin API, 헤더
// 시크릿 읽기를 갖는다. 에이전트는 업로드 문서의 프롬프트 인젝션을, 프로토타입은 에이전트가 쓴
// 코드와 npm 공급망을 안고 돈다. 그 프로세스들은 실행 래퍼 아래에서 IMDS에 닿지 못하고
// (infra/scripts/aipds-launch), 백엔드가 이 롤을 AssumeRole해 루프백 엔드포인트로 넘긴다
// (backend/aipds/credentials.py). 할 수 있는 것은 Bedrock 호출뿐이다(첫 호출의 Marketplace 자동 구독 포함).
//
// **왜 별도 스택인가.** HostingStack을 배포하면 EC2가 교체될 수 있다(lib/aipds-preview-stack.ts와
// 같은 사정). 이 스택은 인스턴스 롤을 ARN으로 참조만 한다. AssumeRole 권한은 인스턴스 롤에
// 붙는 별도 정책(AWS::IAM::Policy)이라 HostingStack 템플릿은 그대로다 — 신뢰 정책만으로
// 충분한지(같은 계정)에 기대지 않는다.
//
// **인스턴스가 이 롤을 아는 법.** ARN을 SSM 파라미터로 쓴다(lib/instance-params.ts). 새 인스턴스는
// 부팅 때 그것을 읽어 래퍼를 켜고 IMDS를 막는다 — 교체된 인스턴스가 운영자를 기다리는 동안
// 격리 없이 도는 틈이 없다.

export interface AgentCredsStackProps extends cdk.StackProps {
  /** HostingStack의 인스턴스 롤. 이 롤이 AgentRole을 AssumeRole한다. */
  instanceRoleArn: string;
}

export class AipdsAgentCredsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCredsStackProps) {
    super(scope, id, props);

    const agentRole = new iam.Role(this, 'AgentRole', {
      description: 'AI-PDS sandboxed agents and prototypes: Bedrock invoke only.',
      assumedBy: new iam.ArnPrincipal(props.instanceRoleArn),
      // 인스턴스 롤 → 이 롤은 롤 체이닝이라 세션은 어차피 1시간이 상한이다.
      maxSessionDuration: cdk.Duration.hours(1),
    });
    agentRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODEL_ARNS(this.account),
    }));
    // 첫 호출의 Marketplace 자동 구독(lib/backend-permissions.ts).
    agentRole.addToPolicy(bedrockSubscribeStatement());

    const instanceRole = iam.Role.fromRoleArn(this, 'InstanceRole', props.instanceRoleArn, {
      mutable: true,
    });
    agentRole.grantAssumeRole(instanceRole);
    // 인스턴스 롤도 Bedrock을 직접 부른다(설문 생성 등). HostingStack이 backendPolicyStatements로 이미
    // 주지만, 그 스택을 다시 배포하지 않는 환경(README 9단계)의 인스턴스 롤은 이 스택이 아니면 받을 길이
    // 없다. 같은 문장이 두 번 붙어도 권한은 같다.
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
