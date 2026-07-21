# Pathfinder Drill Infra (CDK, 기본 ap-northeast-2 / 서울)

Single stack `PathfinderDrillStack`: S3 artifacts bucket (`projects/*` +
`sessions/*`) and a backend execution role (Bedrock invoke + S3 access on
both prefixes). The Strands agent now runs in-process in the backend — there
is no MicroVM image, build role, harness asset, or log group anymore.

## 리전 (파라미터)
기본 배포 리전은 **서울(`ap-northeast-2`)**. 도쿄는 Lambda MicroVMs 때문에 강제됐던
것으로, VM이 사라진 지금은 필요 없다. 다른 리전에 배포하려면 `CDK_DEPLOY_REGION`
(또는 프로파일이 채워주는 `CDK_DEFAULT_REGION`)으로 오버라이드한다:
```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy   # 예: 도쿄로 배포
```
Bedrock 인퍼런스 프로파일(`global.anthropic.claude-opus-4-8`)은 글로벌 프로파일이고
IAM 리소스 ARN도 리전 와일드카드라, 리전을 바꿔도 스택은 그대로 동작한다.

## Synth / deploy
```bash
npm ci
npx cdk synth                 # validates the stack (no AWS creds needed)
npx cdk deploy                # creates the bucket + backend role (기본 서울)
```
Feed the CfnOutputs into the backend env: `BackendRoleArn`→ the role the
backend process assumes (or the instance-profile trust is narrowed to it in
production — `AccountPrincipal` here is drill/demo convenience only),
`ArtifactsBucketName`→`PATHFINDER_S3_BUCKET`, `Region`→`AWS_REGION`/`PATHFINDER_S3_REGION`
(버킷이 만들어진 리전과 반드시 일치시킬 것).
