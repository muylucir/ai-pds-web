import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as path from 'path';
import { backendPolicyStatements, MODEL } from './backend-permissions';
import { renderUserData } from './user-data';

export interface HostingStackProps extends cdk.StackProps {
  artifactsBucket: s3.IBucket;
  // 테스트 주입용. 미지정 시 배포 리전의 CloudFront origin-facing 프리픽스
  // 리스트를 fromLookup으로 자동 조회한다.
  cfPrefixListId?: string;
}

export class PathfinderHostingStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: HostingStackProps) {
    super(scope, id, props);

    // --- VPC: 퍼블릭 서브넷만, NAT 없음(비용 0). ---
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    // --- CloudFront origin-facing 프리픽스 리스트 (리전 자동) ---
    const cfPrefixListId =
      props.cfPrefixListId ??
      ec2.PrefixList.fromLookup(this, 'CfOriginFacing', {
        prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
      }).prefixListId;

    // --- SG: 80만, CloudFront 프리픽스 리스트에서만. SSH 없음. ---
    const sg = new ec2.SecurityGroup(this, 'InstanceSg', {
      vpc,
      allowAllOutbound: true, // 패키지 설치 · Bedrock · S3
      description: 'Pathfinder EC2 — inbound 80 from CloudFront prefix list only.',
    });
    sg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixListId),
      ec2.Port.tcp(80),
      'CloudFront origin-facing only',
    );

    // --- 비밀 헤더 값 (영숫자 32자) ---
    const headerSecret = new secretsmanager.Secret(this, 'OriginVerifyHeader', {
      description: 'X-Origin-Verify shared secret (CloudFront custom header <-> nginx).',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
        // 단일 스칼라 시크릿(문자열 그대로) — JSON 아님.
      },
    });

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- 인스턴스 롤: 백엔드 공통 권한 + 시크릿 읽기 + SSM 접속 ---
    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
      description: 'Pathfinder EC2: Bedrock + artifacts S3 + read header secret.',
    });
    for (const stmt of backendPolicyStatements(props.artifactsBucket, account)) {
      role.addToPolicy(stmt);
    }
    headerSecret.grantRead(role);

    // --- 앱 코드 에셋(리포 zip) ---
    const asset = new assets.Asset(this, 'AppAsset', {
      path: path.join(__dirname, '..', '..'), // 리포 루트
      exclude: [
        '.git', 'infra', 'docs',
        '**/node_modules', '**/.venv', '**/.next', '**/cdk.out',
        '**/__pycache__', '**/*.egg-info', '**/test-results',
        '**/playwright-report', 'files/*.png',
        '**/.env', '**/.env.*',
      ],
    });
    asset.grantRead(role);

    // --- user-data ---
    const userData = ec2.UserData.custom(
      renderUserData({
        region,
        bucketName: props.artifactsBucket.bucketName,
        model: MODEL,
        secretArn: headerSecret.secretArn,
        assetS3Uri: asset.s3ObjectUrl, // s3://bucket/key
      }),
    );

    // --- 인스턴스 (AL2023 arm64/Graviton, IMDSv2 강제) ---
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64,
      }),
      securityGroup: sg,
      role,
      userData,
      requireImdsv2: true,
      userDataCausesReplacement: true, // 에셋(코드) 변경 시 깨끗한 재부트스트랩
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(20, { encrypted: true }),
      }],
    });

    // --- 고정 EIP (재배포에도 CloudFront 오리진 도메인 불변) ---
    const eip = new ec2.CfnEIP(this, 'Eip', { domain: 'vpc' });
    new ec2.CfnEIPAssociation(this, 'EipAssoc', {
      allocationId: eip.attrAllocationId,
      instanceId: instance.instanceId,
    });

    // EIP IP -> 퍼블릭 DNS 이름 (CloudFront 오리진은 도메인만 허용; IP 불가).
    //   ec2-<a-b-c-d>.<region>.compute.amazonaws.com  (us-east-1은 compute-1)
    const computeDomain =
      region === 'us-east-1' ? 'compute-1.amazonaws.com' : `${region}.compute.amazonaws.com`;
    const originDnsName =
      `ec2-${cdk.Fn.join('-', cdk.Fn.split('.', eip.attrPublicIp))}.${computeDomain}`;

    // 비밀 헤더 값: CFN dynamic reference({{resolve:secretsmanager:...}})로 주입.
    // 배포 시 CloudFormation이 해석 → 템플릿에는 평문이 남지 않음.
    const origin = new origins.HttpOrigin(originDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      readTimeout: cdk.Duration.seconds(60), // SSE(백엔드 ping 15s) 여유
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        'X-Origin-Verify': headerSecret.secretValue.unsafeUnwrap(),
      },
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Pathfinder — CloudFront in front of EC2 (header-authenticated origin).',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
      additionalBehaviors: {
        '/_next/static/*': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED, // 해시 파일명 → 불변
        },
      },
    });

    new cdk.CfnOutput(this, 'DistributionDomain', {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'EipAddress', { value: eip.attrPublicIp });
  }
}
