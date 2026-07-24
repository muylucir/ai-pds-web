# 프로토타입 생성 기능 — 수동 E2E 체크리스트

Discovery 스펙(`PROTOTYPE-{slug}.md`) → Tokyo MicroVM(Claude Agent SDK 하네스) 빌드 →
S3 번들 영속화 → Pathfinder EC2 로컬 호스팅 → 경로 프록시 프리뷰 전체 경로를 실 AWS
자원으로 검증한다. 실 VM·실 Bedrock·실 EC2가 필요해 CI에는 없다(단위 테스트는
`backend/tests/test_proto_*.py`, `harness/tests/`, `infra/test/vm-stack.assert.ts`가
fake/Stubber로 커버). 설계는
`docs/superpowers/specs/2026-07-24-prototype-generation-design.md`, 구현 계획은
`docs/superpowers/plans/2026-07-24-prototype-generation.md` 참고.

전제: `infra/README.md`의 "PathfinderVmStack 배포 절차"를 먼저 훑을 것. 아래 (a)는 그
요약이다.

---

## (a) VmStack 배포 → 이미지 빌드 확인

- [ ] `files/aiplc-rules/`가 드릴 머신에 채워져 있다(gitignored 참고 자료 —
      `files/aiplc-rules/aws-aiplc-rules/core-workflow.md` 존재 확인).
- [ ] 하네스 코드를 스테이징한다:
      ```bash
      cd infra
      ./package-harness.sh
      ```
      (`files/aiplc-rules/` 누락 시 이 스크립트가 즉시 에러로 종료한다 — 정상 동작.)
- [ ] VmStack을 배포한다. **`CDK_DEPLOY_REGION`을 설정했든 안 했든 무관** — 이
      스택은 항상 Tokyo(`ap-northeast-1`)에 배포된다(`bin/app.ts` 하드코딩):
      ```bash
      npx cdk deploy PathfinderVmStack --require-approval never
      ```
- [ ] 출력된 CfnOutputs(`ImageArn`, `ExecutionRoleArn`, `Region`)를 기록해둔다 — (c)에서 씀.
- [ ] CloudWatch 로그 그룹 `/pathfinder/microvm/harness`에서 이미지 빌드 로그를 연다.
      `ready hook: health=True | ...` 라인이 나오면 빌드 스냅샷 성공(빌드 자체는 수 분
      걸릴 수 있음). `health=False`가 반복되면 하네스 앱(`serve.py`, 포트 8080/9000)이
      기동하지 못한 것 — Dockerfile/`requirements.txt` 문제부터 확인.

## (b) sdk_diagnostic 로그 확인 (번들 바이너리 arch/import OK)

- [ ] 같은 로그 라인의 `|` 뒤쪽이 `sdk_diagnostic()`의 출력이다(`harness/hooks.py`).
      정상: `sdk <version>; PATH claude=absent (bundled binary is used)` 형태 —
      `claude_agent_sdk`가 import되고, 번들 CLI 바이너리는 SDK가 직접 스폰하므로
      `PATH`에는 없는 게 정상(경고 아님).
      비정상: `claude_agent_sdk import failed <ExceptionType>: ...` — ARM_64 이미지에
      x86 wheel이 섞였거나 `requirements.txt`의 `claude-agent-sdk==` 핀이 이 아키텍처용
      바이너리를 못 받은 경우. 이 로그는 **빌드 게이트가 아니라 진단 전용**이므로
      (`sdk_diagnostic` 자체가 실패해도 `/ready`는 서버 헬스만으로 200을 줄 수 있음)
      빌드가 성공해도 반드시 이 라인을 직접 확인해야 한다.

## (c) 백엔드 env 주입

- [ ] `backend/.env`(또는 실 프로세스 환경)에 4개 변수를 채운다(`backend/.env.example`
      참고):
      ```
      PATHFINDER_VM_REGION=ap-northeast-1
      PATHFINDER_VM_IMAGE_ID=<(a)의 ImageArn 출력>
      PATHFINDER_VM_ROLE_ARN=<(a)의 ExecutionRoleArn 출력>
      PATHFINDER_PROTO_ROOT=~/pathfinder-protos   # 기본값, 필요 시만 변경
      ```
- [ ] 백엔드를 (재)기동해 값이 반영됐는지 확인 — 미설정 상태로 세션을 시작하면
      `POST .../session`이 502(부팅 실패)를 낸다(`vm.py`의 `run_microvm`이
      `image_id=None`으로 호출되어 실패).

## (d) microvm control IAM 액션 실배포 검증 (doc-verified only)

- [ ] 백엔드가 실행되는 롤(드릴 백엔드 롤 또는 호스팅 EC2 인스턴스 롤 — 둘 다
      `infra/lib/backend-permissions.ts`의 `microvmControlStatements`를 이미 포함)
      자격증명으로 `list_microvms`가 AccessDenied 없이 응답하는지 확인:
      ```bash
      aws lambda-microvms list-microvms --region ap-northeast-1
      ```
      IAM 액션 네임스페이스는 `lambda-microvms:`가 아니라 `lambda:`임에 유의
      (`backend-permissions.ts` 주석 — boto3/CLI 서비스명과 IAM 액션 프리픽스가 다르다).
      **이 항목은 doc-verified만 요구** — 별도 스택 재배포 없이, 이미 배포된 드릴/호스팅
      스택의 롤이 이 정책을 갖고 있음을 위 CLI 콜 1회로 확인하면 충분하다.

## (e) 프로토타입 탭 — 빌드 세션 전체 왕복

- [ ] 프론트 프로젝트의 "프로토타입" 탭(`/projects/{projectId}/prototypes`)을 연다.
      Discovery에서 나온 `PROTOTYPE-{slug}.md` 스펙이 카드로 보이는지 확인
      (`상태: 스펙만 있음`).
- [ ] "빌드 시작" 클릭 → `POST /session` 202 확인(VM 부팅 폴링 상한 90초 — 이 안에
      끝나야 함). 빌드 패널이 열리고 **첫 턴이 자동으로 스트리밍**되는지 확인
      (`events?text=__first__` → 서버가 `first_prompt()`로 치환).
- [ ] 첫 턴 스트림 중 메시지/상태/파일 변경 이벤트가 채팅에 실시간으로 렌더되는지
      확인. 우측 "파일 변경 목록"이 `file_changed` 이벤트마다 누적되는지 확인.
- [ ] 에이전트가 `AskUserQuestion`을 호출하는 지점까지 대화를 진행 → 우측 패널에
      질문 위저드(`QuestionForm`)가 뜨는지 확인 → 옵션 중 하나를 **레터로 답변**
      → 제출 → **같은 스트림이 이어져서** 턴이 계속 진행되는지 확인(질문 왕복이
      새 스트림을 열지 않음 — SSE가 열린 채 대기하다가 답변으로 재개).
- [ ] 턴이 진행 중일 때 "중단" 버튼이 보이는지 확인 → 클릭 → `POST /interrupt` 202
      → 스트림에 `status: "interrupted"` 이벤트 후 `done`이 오고 스트리밍 상태가
      꺼지는지 확인. 중단 후에도 세션이 죽지 않고(카드가 "실패"로 안 바뀜) 새
      메시지를 보낼 수 있는지 확인.
- [ ] 빌드가 만족스러우면 "완료" 버튼 클릭 → `DELETE /session` 204 → 패널이 닫히고
      목록이 새로고침되는지 확인.
- [ ] S3에서 번들 확인:
      ```bash
      aws s3 ls s3://<ArtifactsBucketName>/projects/<pid>/prototypes/<slug>/bundle/ --recursive
      ```
      `node_modules/`, `.next/`, `.git/` 세그먼트를 포함한 키가 **없어야** 한다
      (`session.py`의 `_EXCLUDED_SEGMENTS`). 카드 상태가 "빌드 완료"로 바뀌었는지도
      확인.

## (f) 재빌드 — 번들 복원 확인

- [ ] "빌드 완료" 카드에서 "다시 빌드" 클릭 → 새 세션 시작(이번엔 새 VM +
      **이전 번들을 그 VM에 복원**) → 빌드 패널이 열리되 이번엔 **자동 첫 턴이
      발화되지 않아야** 함(재시작이 아니라 신규 세션이므로 `autoStart=true`로
      다시 열리는 게 맞음 — 실제로는 "다시 빌드"도 `handleBuild`를 그대로 타므로
      `startSession`이 202를 반환하면 `autoStart=true`가 된다. 즉 이 경로에서는
      **첫 턴이 다시 발화**되고, 그 첫 턴에서 에이전트가 기존 `/workspace/prototype/`
      내용을 그대로 보고 이어서 작업한다 — 발화 자체가 재트리거되는 것과 번들
      내용이 복원되는 것은 별개임에 유의).
- [ ] 첫 턴에서 에이전트가 이전에 만든 파일들을 인지하고 있는지(예: "기존
      README/코드를 확인했다"는 취지의 응답이나, 실제로 새 코드를 처음부터 다시
      만들지 않는지)로 번들 복원을 간접 확인. 확실한 확인은 VM에 직접 파일 목록을
      물어보게 하거나, 완료 후 S3 번들 diff가 "추가 변경만" 반영됐는지 보는 것.

## (g) 호스팅 — start → 프리뷰 → 프록시 하위 동작 → 로그 tail → stop

- [ ] "빌드 완료" 카드에서 "호스팅 시작" 클릭 → `POST /host` → 상태가
      `installing` → (package.json에 `build` 스크립트가 있으면) `building` →
      `running`으로 전이하는지 확인. 실패 시 502 + 로그 tail이 노출되는지 확인.
- [ ] 카드가 "실행 중 :<port>"로 바뀌는지 확인(포트는 4001부터 순차 스캔).
- [ ] "프리뷰 열기" 클릭 → 새 탭이 **CloudFront 경유** `/api/proto/{pid}/{slug}/`
      URL로 열리는지 확인(`prototypePreviewUrl` — 로컬 개발이면 `http://localhost:8000`,
      배포 환경이면 `https://<CloudFront 도메인>/api/proto/...`). nginx가 `/api/` 를
      벗겨 백엔드(:8000)로 넘기고, 백엔드의 `proxy_prototype` 라우트가
      `http://127.0.0.1:<port>/...`로 스트리밍 중계한다.
- [ ] **basePath/상대 경로 하위 동작 확인**: 프리뷰 URL 하위의 정적 자산(JS/CSS)과
      내부 링크/API 호출이 절대 경로(`/`)가 아니라 `/api/proto/{pid}/{slug}/` 접두를
      유지한 채 동작하는지 확인(첫 턴 지침 4번이 에이전트에게 이걸 요구했음 — 만약
      절대 경로를 하드코딩했다면 자산 404/API 404가 난다). 브라우저 개발자도구
      Network 탭에서 요청 경로를 확인.
- [ ] 카드의 "로그" 버튼 → `GET /host` 의 `log_tail`이 최근 로그(최대 100줄)를
      보여주는지 확인.
- [ ] "호스팅 중지" 클릭 → `DELETE /host` 204 → 상태가 `stopped`로 바뀌고 프리뷰
      접속 시 502(안내 페이지)로 바뀌는지 확인.

## (h) 유휴 자동 종료 (빌드 세션 30분)

- [ ] 빌드 세션을 열어둔 채(카드가 "빌드 중") 아무 턴도 보내지 않고 30분(또는
      로컬 검증 시 `PrototypeSession(idle_seconds=...)`을 임시로 짧게 바꿔 재현)
      대기 → 세션이 자동으로 `close()`되어(S3 번들 sync + VM stop) 카드가 "빌드
      완료"로 복귀하는지 확인.
      참고: `idle_seconds`는 env로 노출되어 있지 않다(코드 기본값 1800초) — 짧게
      테스트하려면 `backend/pathfinder/app.py`의 `proto_session_factory`가
      `PrototypeSession(...)`을 만드는 지점(`backend/pathfinder/proto/session.py`의
      생성자)에 임시로 `idle_seconds=짧은값`을 넘기는 로컬 패치를 쓰고 **커밋하지
      않는다**.
- [ ] 질문 대기 중(`waiting_input`) 유휴 만료도 같은 방식으로 재현 가능하면 확인 —
      pending future가 소멸하고 세션이 닫히는지(질문 자체는 유실 — 스펙상 수용된
      동작).

## (i) 백엔드 재시작 → 고아 VM 정리 로그

- [ ] 빌드 세션이 살아있는(VM이 RUNNING인) 상태에서 백엔드 프로세스를 재시작한다.
- [ ] 기동 로그에서 `terminated %d orphan prototype VM(s)`(`app.py`의
      `_cleanup_orphan_vms`) 라인이 찍히는지 확인. `PATHFINDER_VM_IMAGE_ID`가
      설정 안 됐거나 `fake-`로 시작하면 이 정리는 스킵된다(정상 — 로컬/테스트용
      가드).
- [ ] `aws lambda-microvms list-microvms --region ap-northeast-1`로, 재시작 전
      떠 있던 VM(`imageArn`이 `PATHFINDER_VM_IMAGE_ID`와 일치하고 `RUNNING`이던 것)이
      실제로 terminate됐는지 확인. 이 정리는 **태그가 아니라 imageArn 필터**를
      쓴다(MicroVM에 태깅 API가 없음 — `_cleanup_orphan_vms` 주석).
- [ ] 재시작으로 소멸한 인메모리 `proto_sessions` 레지스트리 때문에 프론트
      카드가 일시적으로 "빌드 중"이 아니라 다른 상태로 보일 수 있음(인메모리
      상태 소실은 알려진 설계 범위 — §6 에러 표 "백엔드 재시작" 행) — 새로고침
      후 실제 S3/호스팅 상태 기준으로 정상 표시되는지 확인.

## (j) 플랫폼 auto-suspend/auto-resume 사이클

- [ ] `BootSpec.idle_policy()`가 보내는 `maxIdleDurationSeconds=300`(5분) 동안
      VM에 아무 요청도 안 가면, AWS 플랫폼이 자체적으로 VM을 SUSPENDED로 전환하는지
      `get-microvm`으로 확인:
      ```bash
      aws lambda-microvms get-microvm --microvm-identifier <id> --region ap-northeast-1
      ```
      (이 컨트롤러에는 `suspend()`/`resume()` 메서드가 없다 — 순전히 플랫폼이
      idle-policy에 따라 자동으로 하는 동작이다.)
- [ ] SUSPENDED 상태에서 그 VM의 엔드포인트로 다시 요청을 보내면(`autoResumeEnabled:
      True`) 플랫폼이 투명하게 재기동해 정상 응답하는지 확인 — 백엔드/하네스 코드
      변경 없이 이 사이클이 끝단(엔드포인트 안정성)에서 그대로 동작해야 한다.
- [ ] `suspendedDurationSeconds=1800`(30분)을 넘기면 VM이 TERMINATED로 전환되는지도
      확인(선택 — 대기 시간이 길다).

## (k) 409-reopen 시 빈 채팅 (알려진 제약)

- [ ] 빌드 세션이 살아있는 카드에서 패널을 닫고(또는 닫지 않고) 다시 "세션
      열기"를 클릭한다 → `POST /session`이 409(이미 활성 세션)를 반환 → 프론트가
      이를 흡수하고(`autoStart=false`) 같은 세션의 패널을 새로 연다.
- [ ] **알려진 제약을 확인**: 이때 채팅 타임라인(`items`)이 **비어서 시작**하는지
      확인 — `usePrototypeStream`은 마운트마다 항상 빈 배열로 시작하고, Task 7
      라우트에는 프로토타입 세션의 히스토리 복원 엔드포인트가 없다. 즉 서버 쪽
      세션 상태(`status`, VM 핸들 등)는 유지되지만 **과거 턴의 텍스트/트레이스는
      프론트에 재생되지 않는다** — 새로고침/재오픈 전 대화가 전부 사라진 것처럼
      보이는 게 정상(회귀 아님).
- [ ] 재오픈 시점에 마침 질문이 대기 중이었던 경우도 확인: 서버는
      `_pending_interrupt_id`를 세션 객체에 들고 있지만, 재오픈한 패널은 그 상태를
      가져올 방법이 없다(질문 위저드 mount 트리거는 오직 열린 이벤트 스트림의
      `questions` 프레임뿐). 따라서 **질문 위저드도 다시 뜨지 않는다** — 사용자는
      빈 채팅에 새 메시지를 입력하게 되는데, 이 경우 서버가 미해결 질문을 안은 채
      새 턴을 받아들이는지/에러를 내는지 실제로 관찰해 기록한다(현재 구현상
      `send_message`는 대기 중 질문 유무를 검사하지 않음 — 하네스/SDK 쪽의
      실제 동작이 문서화되지 않은 부분이므로 이번 e2e에서 처음 확인).
