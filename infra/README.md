# Pathfinder Infra (CDK, 기본 ap-northeast-2 / 서울)

두 스택:

- **PathfinderDrillStack** — S3 아티팩트 버킷(`projects/*` + `sessions/*`) +
  백엔드 실행 롤(Bedrock invoke + S3). 인프로세스 Strands 에이전트용.
- **PathfinderHostingStack** — VPC + EC2(AL2023 arm64) + CloudFront. EC2는
  CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전 자동)에서만 80을
  받고, CloudFront가 붙이는 비밀 헤더 `X-Origin-Verify`를 nginx가 검증한다.
  user-data가 리포 에셋을 받아 백엔드/프론트를 빌드·기동한다.

## 리전 (파라미터)
기본 서울(`ap-northeast-2`). 다른 리전은 `CDK_DEPLOY_REGION`으로 오버라이드:
```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all   # 예: 도쿄
```
프리픽스 리스트 ID는 리전마다 다르지만 `PrefixList.fromLookup`이 배포 리전의
ID를 자동 조회하므로 코드 수정이 필요 없다.

## 테스트
```bash
npm ci
npm test            # user-data 순수함수 + 스택 어서션 (크리덴셜 불필요)
```

## Synth / deploy
```bash
npx cdk synth PathfinderDrillStack      # 크리덴셜 불필요
npx cdk synth PathfinderHostingStack    # 프리픽스 리스트 lookup — 크리덴셜 필요(최초 1회)
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 최초 1회
npx cdk deploy --all --require-approval never
```
> 호스팅 스택은 배포 리전의 CloudFront 프리픽스 리스트를 lookup하므로 첫
> synth/deploy에 계정 크리덴셜이 필요하다. 조회 결과는 `cdk.context.json`에
> 캐시되며 **커밋**한다(재현성). EC2 첫 부팅 빌드에 ~5–10분 걸리므로 배포
> 완료 직후 CloudFront가 잠시 502를 반환할 수 있다(정상).

## 출력 (CfnOutputs)
- `PathfinderHostingStack.DistributionDomain` — 접속 URL(`https://dxxxx.cloudfront.net`)
- `PathfinderHostingStack.InstanceId` — SSM 접속: `aws ssm start-session --target <id>`
- `PathfinderHostingStack.EipAddress` — 오리진 IP(디버그)
- `PathfinderDrillStack.ArtifactsBucketName` / `BackendRoleArn` / `Region`

## 접속 · 검증
- 브라우저 → `DistributionDomain`(HTTPS) → CloudFront → EC2 nginx.
- EC2에는 SSH 포트가 열려있지 않다 — `aws ssm start-session --target <InstanceId>`.
- 오리진 직접 접근은 SG(프리픽스 리스트)로 차단되고, 설령 도달해도 nginx가
  헤더 없으면 403.
