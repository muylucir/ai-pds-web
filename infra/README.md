# Pathfinder Infra (CDK, 기본 ap-northeast-2 / 서울)

세 스택:

- **PathfinderDrillStack** — S3 아티팩트 버킷(`projects/*` + `sessions/*`) +
  백엔드 실행 롤(Bedrock invoke + S3 + MicroVM 제어). 인프로세스 Strands
  에이전트용.
- **PathfinderHostingStack** — VPC + EC2(AL2023 arm64) + CloudFront. EC2는
  CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전 자동)에서만 80을
  받고, CloudFront가 붙이는 비밀 헤더 `X-Origin-Verify`를 nginx가 검증한다.
  user-data가 리포 에셋을 받아 백엔드/프론트를 빌드·기동한다.
- **PathfinderVmStack** — 프로토타입 생성 기능(프론트 "프로토타입" 탭)이 쓰는
  Claude Agent SDK 하네스 MicroVM 이미지 + 실행 롤. **항상 Tokyo
  (`ap-northeast-1`) 고정 배포** — `lambda-microvms` 서비스가 도쿄에만 있어서
  (2026-07-17 확인: `list-microvm-images`가 서울에서 AccessDeniedException).
  위 두 스택의 리전 파라미터(`CDK_DEPLOY_REGION`)와는 **무관**하다 —
  `bin/app.ts`가 이 스택만 `env.region: 'ap-northeast-1'`을 하드코딩한다.

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
- `PathfinderVmStack.ImageArn` / `ExecutionRoleArn` / `Region` — 아래 절 참고

## 접속 · 검증
- 브라우저 → `DistributionDomain`(HTTPS) → CloudFront → EC2 nginx.
- EC2에는 SSH 포트가 열려있지 않다 — `aws ssm start-session --target <InstanceId>`.
- 오리진 직접 접근은 SG(프리픽스 리스트)로 차단되고, 설령 도달해도 nginx가
  헤더 없으면 403.

## PathfinderVmStack 배포 절차 (프로토타입 생성 하네스, Tokyo 고정)

프로토타입 생성 기능은 Discovery 스펙(`PROTOTYPE-{slug}.md`)을 읽어 Tokyo
MicroVM 안의 Claude Agent SDK가 대화형으로 앱을 빌드하게 한다(하네스는
`harness/`). 이 스택은 그 MicroVM 이미지와 실행 롤만 배포한다 — 아티팩트는
여전히 서울 `PathfinderDrillStack`의 버킷을 쓴다(파일은 백엔드가 HTTP로 중개,
VM은 S3에 직접 접근하지 않음).

1. **하네스 코드 스테이징** — `harness/`와 `rule/aiplc-rules/`를 CDK 에셋이
   기대하는 모양(`Dockerfile`이 zip 루트)으로 `infra/build/harness/`에 복사:
   ```bash
   cd infra
   ./package-harness.sh
   ```
   `rule/aiplc-rules/`는 리포에 포함되어 있어 체크아웃만 하면 준비된다(과거
   `files/aiplc-rules/`는 gitignored여서 머신마다 수동 배치가 필요했다).
   트리가 손상·누락된 경우
   `rule/aiplc-rules/aws-aiplc-rules/core-workflow.md` 부재를 감지해
   `package-harness.sh`가 즉시 에러로 종료한다.
2. **배포** — 리전 파라미터는 무시하고 항상 도쿄:
   ```bash
   npx cdk deploy PathfinderVmStack --require-approval never
   ```
   (`CDK_DEPLOY_REGION`을 설정해도, 설정하지 않아도 이 스택은 항상
   `ap-northeast-1`에 배포된다 — `bin/app.ts`가 하드코딩.)
3. **출력을 백엔드 env로 수동 주입** — `ImageArn` → `PATHFINDER_VM_IMAGE_ID`,
   `ExecutionRoleArn` → `PATHFINDER_VM_ROLE_ARN`, `Region`
   (`ap-northeast-1`) → `PATHFINDER_VM_REGION`(`backend/.env.example` 참고).
   백엔드(드릴 롤 또는 호스팅 인스턴스 롤)도 `microvmControlStatements`로
   Tokyo MicroVM 제어 액션(`RunMicrovm`/`GetMicrovm`/`TerminateMicrovm`/
   `ListMicrovms`/`CreateMicrovmAuthToken`)을 이미 갖고 있어야 한다 — 드릴/
   호스팅 스택에 이미 합쳐져 있으므로 별도 배포는 불필요.
4. **이미지 빌드 확인** — CloudWatch 로그 그룹 `/pathfinder/microvm/harness`에서
   ready hook의 헬스 로그를 확인한다(빌드는 수 분 걸린다). CLI 진단
   (`sdk_diagnostic` — `claude_agent_sdk` import + 번들 바이너리 arch)은 같은
   로그에 남지만 빌드 게이트는 아니다(서버 헬스만 게이트).

### 이미지 재빌드가 필요한 시점

`harness/` 아래 어떤 `.py`/`Dockerfile`/`requirements.txt`를 바꿔도 **이미
배포된 이미지는 그대로**다 — `CfnMicrovmImage`는 코드 아티팩트의 S3 URL로
빌드 시점에 스냅샷된다. 하네스 코드를 바꿨다면 반드시 위 1~2단계
(`package-harness.sh` → `cdk deploy PathfinderVmStack`)를 다시 실행해야 다음
세션 시작부터 새 코드가 반영된다. `rule/aiplc-rules/`만 바꾼 경우도 동일 —
룰은 이미지에 baked이므로 재배포 없이는 반영되지 않는다(`PrototypeSession`이
세션 시작마다 룰 파일을 VM에 push하기도 하므로, 그 경로로는 이미지 재배포 없이
룰 갱신이 가능하다 — 다만 baked 기본값 자체를 바꾸려면 재배포가 필요).
