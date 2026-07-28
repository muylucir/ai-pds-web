# Discovery 드라이버(Claude Agent SDK) 배포 검증

Strands → Claude Agent SDK로 Discovery 에이전트 드라이버를 교체하는 작업
(`docs/superpowers/plans/2026-07-27-discovery-driver-claude-agent-sdk.md`,
설계는 `docs/superpowers/specs/2026-07-27-discovery-driver-claude-agent-sdk-design.md`)
의 수동 배포 검증이다. 8개 구현 태스크 모두 유닛 테스트 그린(614개 + 인프라
17개 + 프론트 562개)이지만, 그 리뷰 과정에서 **유닛 테스트로 못 박을 수 없어
"실제 워크숍/배포 검증 필요"로 명시적으로 남긴 항목**이 여럿 있다
(`.superpowers/sdd/2026-07-27-discovery-driver-claude-agent-sdk/progress.md`).
이 문서는 그 항목들을 사람이 직접 확인할 수 있는 절차로 바꾼 것이다.
2026-07-26의 미들웨어 Edge 런타임 500이 교훈 — 유닛 테스트가 통과했는데 실제
런타임에서 죽었다.

전제: `cd infra && npx cdk deploy PathfinderHostingStack --require-approval never`
후 EC2 첫 빌드 완료(5~10분, 그 동안 CloudFront 502는 정상). 자세한 배포 절차는
`README.md`의 "배포" 절과 `infra/README.md`를 먼저 훑을 것. 인스턴스 접속:

```bash
aws ssm start-session --target <InstanceId>   # InstanceId = PathfinderHostingStack.InstanceId 출력
```

---

## 1. 배선 확인 (SSM)

- [ ] `systemctl show pathfinder-backend -p Environment | tr ' ' '\n' | grep DISCOVERY`
      → 다음 두 줄이 모두 보인다:
      `PATHFINDER_DISCOVERY_DRIVER=claude`(또는 unset — 기본값이 `claude`이므로
      아예 안 보여도 정상)과
      `PATHFINDER_DISCOVERY_CONFIG_DIR=/opt/pathfinder/discovery-config`
- [ ] 같은 명령에서 `PATHFINDER_PROTO_CONFIG_DIR`도 함께 확인해 **두 경로가
      다른지 눈으로 대조**한다(`/opt/pathfinder/proto-config` vs
      `/opt/pathfinder/discovery-config`) — 같으면 Discovery가 프로토타입
      빌드용 shadcn-design 스킬을 켠 채로 돈다(`claude_driver.py`의
      `CLAUDE_CONFIG_DIR` 주석 참고)
- [ ] `ls /opt/pathfinder/discovery-config/CLAUDE.md` → 존재(Pathfinder
      통합 규약 — UI 접점 지시). 리포의 `discovery-config/`가
      `proto-config/`와 같은 메커니즘(리포 zip 에셋,
      `infra/lib/pathfinder-hosting-stack.ts:123-131`의 제외 목록에 없음)으로
      실리므로 **번들 여부 자체는 이미 확정**이다 — 여기서는 그 파일이
      인스턴스에 실제로 도착했는지만 확인한다
- [ ] `ls /opt/pathfinder/discovery-config/projects/` → 없거나 빈 디렉터리
      (SDK가 만드는 로컬 transcript 사본 — `.gitignore`의 런타임 제외 대상과
      같은 자리인지 확인용, 첫 배포 직후엔 존재하지 않아도 정상)

## 2. 룰 배치 + `setting_sources` 실제 동작 확인 (핵심 미확인 항목 #1)

설계 문서(`design.md:385-387`)가 명시적으로 "미확인"으로 남긴 지점이다:
Discovery 클라이언트는 `setting_sources=["user", "project"]`로 뜨는데, 이
`project` 스코프가 **워크스페이스의 `CLAUDE.md`**(core-workflow.md 사본)를
실제로 읽는지는 문서상의 추정일 뿐 실측된 적이 없다. 못 읽으면 에이전트가
AI-PLC 워크플로우(스테이지 전이·report_stage·질문 규약) 없이 그냥 잡담하듯
응답한다 — 겉보기엔 그럴듯한 대화가 이어지므로 **증상이 은근하다**. 사이드바에
스테이지가 하나도 안 뜨는 것이 유일한 신호일 수 있다.

- [ ] 프로젝트를 하나 만들고 첫 메시지를 보낸다
- [ ] SSM에서 워크스페이스 확인:
      `ls $PATHFINDER_WORKSPACES_DIR/<pid>/` → `CLAUDE.md`,
      `aws-aiplc-rule-details/`, `aiplc-docs/`가 보인다
- [ ] `diff /opt/pathfinder/rule/aiplc-rules/aws-aiplc-rules/core-workflow.md \
       $PATHFINDER_WORKSPACES_DIR/<pid>/CLAUDE.md` → 차이 없음(내용이 룰
      원본과 바이트 단위로 같다는 것만 확인 — SDK가 그걸 **읽는지**는 다음
      항목이 증거다)
- [ ] **결정적 증거**: 첫 턴 응답에서 에이전트가 AI-PLC 워크플로우를 실제로
      따르는 흔적을 확인한다 — report_stage 호출로 사이드바에 스테이지가
      뜨는지(§3), `aiplc-docs/aiplc-state.md`가 생성되는지
      (`ls $PATHFINDER_WORKSPACES_DIR/<pid>/aiplc-docs/aiplc-state.md`).
      **이 파일이 안 생기면 project 스코프가 CLAUDE.md를 못 읽고 있다는
      뜻이다** — 설계가 예고한 정확히 그 실패 모드. 이 경우
      `discovery-config/CLAUDE.md`와 워크스페이스 CLAUDE.md를 합치는 폴백이
      필요하므로 워크숍 전에 코드 수정이 필요하다(에스컬레이션)

## 3. 첫 턴 (Workspace Detection)

> **두 번째 턴부터가 진짜 관문이다.** 최종 리뷰가 잡은 C1이 정확히 여기였다:
> 같은 프로젝트의 session id는 project id에서 uuid5로 파생돼 *안정적*이고,
> CLI의 transcript는 프로세스보다 오래 살아남는다. 그래서 예전 코드는 백엔드
> 재시작 뒤 **모든 평범한 턴**이 `--session-id ... is already in use`로
> connect()에서 죽었고(실측: exit 1), 스스로 낫지도 않았다. 지금은
> `claude_driver.py`의 `_transcript_exists`가 CLI의 transcript 파일을 직접 보고
> `--session-id`/`--resume`을 고른다. §9가 그 왕복을 따로 검증한다.

- [ ] 채팅에 AI 텍스트가 뜬다(빈 말풍선이 아님)
- [ ] **같은 프로젝트에서 두 번째, 세 번째 메시지도 정상 응답한다**(한 턴만
      보고 넘어가지 말 것 — C1은 첫 턴이 아니라 두 번째 턴부터 드러났다)
- [ ] 활동 라인에 **한글** 문구가 뜬다 — "자료를 확인하고 있어요…" 등.
      영어 도구명이 그대로 보이면(`Read 실행 중…` 형태) Task 7의 라벨 매핑이
      누락된 것이다. `frontend/components/canvas/AiMessage.tsx`의
      `ACTIVITY_LABELS`를 확인한다
- [ ] 사이드바에 스테이지가 표시된다(`report_stage` 동작 — §2의 증거와 같음)
- [ ] 우측 패널에 산출물 경로가 쌓인다(PostToolUse 훅 → `file_changed`)

## 4. 질문 왕복

- [ ] 질문 폼이 우측 패널에 뜬다
- [ ] 보기 텍스트가 정상이다 — "Other — 직접 입력"이 **하나만** 있고, 실질
      보기의 텍스트가 사라지지 않았다(`is_other` 중복 회귀 확인, Task 4)
- [ ] 답변을 제출하면 턴이 이어진다

## 5. 새로고침 복원

- [ ] 질문이 떠 있는 상태에서 브라우저를 새로고침한다
- [ ] 질문 폼이 그대로 복원된다
- [ ] 그 폼에 답변하면 정상 진행된다

## 6. 백엔드 재시작 복원 — 재시작 경로의 텍스트-턴 답변 (핵심 미확인 항목 #2)

설계 문서(`design.md:388-389`)가 명시적으로 "실제 워크숍 검증 필요"로 남긴
지점. 백엔드가 재시작되면 pending 질문에 대한 진행 중 future가 사라지므로,
재시작 후 제출된 답변은 **도구 결과가 아니라 평범한 텍스트 턴**으로
`_resume_with_answers`(`claude_driver.py`)가 프롬프트에 조립해 넣는다. 모델이
그 텍스트를 "방금 물어본 질문에 대한 답"으로 이해하고 이어갈지는 오프라인
테스트로 증명할 수 없는 실제 모델 행동이다 — Task 6 리뷰의 세 라운드가 이
경로의 배관(스테일 interrupt_id 가드, 룰 재배치, S3 pending 정리)은 못 박았지만
**모델이 그 문장을 실제로 질문 답변으로 받아들이는지**는 여기서만 확인된다.

- [ ] 질문이 떠 있는 상태에서 SSM으로
      `sudo systemctl restart pathfinder-backend`
- [ ] 브라우저 새로고침 → 질문 폼이 그대로 복원된다(S3 pending 영속 — Task 2)
- [ ] 답변을 제출한다 → **모델이 그 답변을 질문에 대한 답으로 이해하고
      대화를 이어간다**(같은 질문을 다시 하거나, 맥락을 잃고 처음부터
      다시 묻는 것이 아니라). 판단 기준: 응답 텍스트가 그 답변 내용을
      참조하거나, 다음 스테이지로 진행하거나, 관련 산출물을 갱신한다
- [ ] 통과하지 못하면(모델이 맥락을 잃으면) `claude_driver.py`의
      `_resume_with_answers`가 프롬프트에 붙이는 `(답변 기록)` 문구를
      조정한다 — 코드 수정 필요, 워크숍 전 재검증 대상으로 에스컬레이션

## 7. 프로젝트 삭제 → 서브프로세스 정리 확인 (핵심 미확인 항목 #3)

Task 8에서 `AgentRunner.stop()`이 드라이버의 `disconnect()`를 호출하도록
배선됐고(있으면 best-effort로 호출, 없으면 조용히 건너뜀 —
`StrandsDriver`에는 `disconnect`가 없다), `ClaudeDriver.disconnect()`는
서브프로세스 종료 + 인메모리 pending 상태 + **S3 pending 레코드** +
**`_queue`에 남은 미답변 `questions` 이벤트**까지 정리한다(Task 6의 여러
라운드에서 발견된 잔존 문제였다 — carried to Task 8). 유닛 테스트는 fake
드라이버로만 이 배선을 검증했으므로(Task 8 report §4-3), 실제 `claude`
서브프로세스가 진짜로 죽는지는 여기서만 확인된다.

- [ ] 질문이 떠 있는 상태인 프로젝트를 하나 만든다(§4까지 진행)
- [ ] SSM에서 해당 프로젝트의 `claude` 서브프로세스를 찾아 PID를 기록:
      `ps -eo pid,args | grep "[c]laude"`
- [ ] 프론트에서 그 프로젝트를 삭제한다(프로젝트 목록의 삭제 버튼 →
      `DELETE /projects/{pid}`)
- [ ] 기록한 PID가 사라졌는지 확인: `ps -p <PID>` → 없음(또는
      `ps -eo pid,args | grep "[c]laude"`에서 해당 프로젝트 관련 프로세스가
      더 안 보인다)
- [ ] 같은 project_id로 새 프로젝트를 다시 만들고 첫 메시지를 보낸다 →
      **죽은 질문 카드가 뜨지 않는다**(옛 프로젝트가 남긴 미답변 질문이
      새 프로젝트의 첫 턴에 다시 나타나면 `disconnect()`의 큐/S3 정리가
      깨진 것이다 — Task 8이 고친 정확히 그 회귀)
- [ ] **그 새 프로젝트에서 턴이 실제로 성공한다**(응답이 오고 `agent turn
      failed`가 아니다). 이것이 C1의 두 번째 방아쇠이고 재시작이 전혀 필요 없다:
      삭제된 프로젝트의 transcript는 남아 있는데 같은 project_id는 같은 uuid5
      session id를 파생하므로, 수정 전에는 여기서 `--session-id ... already in
      use`로 죽었다. 두 번째 메시지까지 보낼 것

## 8. 기동 env 가드 — 오타는 트래픽을 받기 전에 죽어야 한다 (핵심 미확인 항목 #4)

로컬에서는 `TestClient`로 uvicorn exit code 3("Application startup failed.
Exiting.")까지 실측됐다(Task 8 report). 여기서는 **실제 배포된 유닛에서도**
그 가드가 서비스를 시작조차 못 하게 막는지 확인한다 — 헬스체크가 200을 보고
"정상"이라 판정한 뒤 첫 참가자가 프로젝트를 열 때야 죽는 것이 이 가드가
막으려는 정확한 실패 모드다.

- [ ] SSM에서 오타를 주입: `sudo systemctl set-environment
      PATHFINDER_DISCOVERY_DRIVER=claud`
- [ ] `sudo systemctl restart pathfinder-backend`
- [ ] `systemctl is-active pathfinder-backend` → `failed`(또는
      `activating`을 거쳐 결국 `failed` — `Restart=always`라
      crash-loop처럼 반복 재시작될 수 있으니 몇 초 후 다시 확인)
- [ ] `journalctl -u pathfinder-backend -n 50 --no-pager` →
      `Application startup failed. Exiting.`과
      `unknown PATHFINDER_DISCOVERY_DRIVER 'claud'` 메시지가 보인다
- [ ] CloudFront/nginx 경유로 접속 시 502(백엔드가 서빙 중이 아님) — "정상
      떠 있는데 첫 요청에서만 죽는" 모양이 **아님**을 확인
- [ ] 원복: `sudo systemctl unset-environment PATHFINDER_DISCOVERY_DRIVER`
      (또는 `systemctl set-environment PATHFINDER_DISCOVERY_DRIVER=claude`) →
      `sudo systemctl restart pathfinder-backend` →
      `systemctl is-active pathfinder-backend` → `active`,
      `GET /projects`가 정상 응답

## 9. 세션 id + `--resume` 판단 — 재시작 후 턴이 살아 있고 맥락도 잇는지 (핵심 미확인 항목 #5)

Discovery의 project id는 자유 형식 문자열이지만 CLI의 `--session-id`는 UUID를
요구한다(`claude --session-id=pilot1 -p hi` → `Error: Invalid session ID.`).
`claude_driver.py`의 `_sdk_session_id`가 `uuid5(NAMESPACE_URL,
"pathfinder:<project_id>")`로 파생해 재시작 전후로 같은 UUID를 만든다.

**최종 리뷰(C1)가 밝힌 것: 그 안정성만으로는 오히려 100% 실패한다.** 두 플래그의
실패 조건이 서로의 여집합이고(번들 2.1.220 실측), 둘 다 connect()에서
서브프로세스를 죽여 `agent turn failed`로만 보인다:

| 상황 | 결과 |
| --- | --- |
| `--session-id=<id>`, transcript **있음** | exit 1 `Session ID ... is already in use.` |
| `--resume=<id>`, transcript **없음** | exit 1 `No conversation found with session ID: ...` |

그래서 `resume=True`를 무조건 주는 것도 답이 아니다. 지금 코드는
`_transcript_exists`로 **CLI 자신의 transcript 파일**
(`$PATHFINDER_DISCOVERY_CONFIG_DIR/projects/<인코딩된 cwd>/<uuid>.jsonl`,
cwd의 `[A-Za-z0-9-]` 이외 문자는 전부 `-`)이 있는지 보고 고른다 — 그 파일의
존재가 CLI의 "already in use" 판정과 정확히 같다는 것도 실측했다(그 `.jsonl`을
치우자 방금 거절당한 `--session-id`가 다시 성공했다).

- [ ] project id가 UUID가 **아닌**(예: 사람이 붙인 이름) 프로젝트를 만들고
      몇 턴 대화해 맥락을 쌓는다(예: 특정 산업/제품명을 언급)
- [ ] SSM에서 transcript가 실제로 그 자리에 생겼는지 본다:
      `ls /opt/pathfinder/discovery-config/projects/*/` → `<uuid>.jsonl` 하나
      (§1에서 "없거나 빈 디렉터리"였던 그 경로다 — 첫 턴 뒤에는 있어야 한다)
- [ ] SSM으로 `sudo systemctl restart pathfinder-backend`
- [ ] 같은 프로젝트로 돌아가 이전 대화를 언급하지 않은 채 이어서 질문한다
      (예: "방금 얘기한 내용 기준으로 다음 단계 진행해줘")
- [ ] **턴이 애초에 성공한다** — `agent turn failed`가 아니다. 이것이 C1의
      1차 증거다(수정 전에는 재시작 후 모든 평범한 턴이 여기서 죽었고, 영구히
      낫지 않았다)
- [ ] 에이전트가 **이전 대화 맥락을 참조**하며 응답하면 통과 — 처음부터
      다시 묻거나 맥락을 잃은 것처럼 반응하면 `--resume`이 옛 transcript를
      못 찾은 것(orphan)이다
- [ ] **재시작을 한 번 더** 하고 같은 확인을 반복한다(2회차에도 같아야
      "한 번 우연히 통과"가 아니다)
- [ ] `journalctl -u pathfinder-backend | grep -i "resume\|session"` →
      `already in use` / `No conversation found` 가 **없다**. 정상 동작 시에는
      드라이버가 판단 결과를 남긴다(`resume=True for session <uuid> ...
      transcript found`) — 이 줄이 그 판단의 직접 증거다
- [ ] **transcript가 사라진 경우도 안전한지**(인스턴스 교체·`/opt` 초기화가
      만드는 상태): 질문이 떠 있는 프로젝트에서
      `sudo mv /opt/pathfinder/discovery-config/projects /tmp/proj-backup` 후
      `restart` → 답변을 제출한다 → **`agent turn failed`가 아니라 정상 진행**
      (맥락은 잃을 수 있다. 여기서 보는 것은 `--resume`이 없는 transcript를
      찾다 죽지 않는다는 것이다). 확인 후
      `sudo mv /tmp/proj-backup /opt/pathfinder/discovery-config/projects`로 원복

## 10. 도구명 활동 라벨 — WebFetch 포함 전수 확인 (핵심 미확인 항목 #6)

`claude_driver.py`가 `tools=`/`disallowed_tools=`를 설정하지 않아(Task 7
리뷰가 지적) CLI의 내장 도구 전체가 열려 있다. Envision의 "URL로 분석"
경로(Mode B/C)는 `WebFetch`가 필수이므로, 사용자가 그 경로를 타면 실제로
`WebFetch`가 호출된다 — raw 영어 도구명이 그대로 노출되면 회귀다.

- [ ] Envision 단계에서 "URL로 분석" 경로를 선택하고 URL 하나를 제공한다
- [ ] 활동 라인에 **"정보를 수집하고 있어요…"**(WebFetch 매핑,
      `AiMessage.tsx`) 가 뜬다 — `WebFetch 실행 중…` 같은 raw 영어가 보이면
      Task 7 매핑이 깨진 것이다
- [ ] 같은 대화에서 파일 탐색이 일어나는 지점(workspace-detection 등)을
      지나며 다른 도구명도 훑는다 — 최소 `Read`("자료를 확인하고
      있어요…"), `Glob`/`Grep`("자료를 찾고 있어요…"), `Write`/`Edit`
      ("문서를 작성하고 있어요…")가 한글로 보이는지
- [ ] 대화 전체에서 **raw 영어 도구명이 한 번도 그대로 노출되지 않는다**
      (`<도구명> 실행 중…` 형태가 한 번도 안 보임 — 보이면 그 도구명을
      `ACTIVITY_LABELS`에 추가해야 한다는 뜻)

## 11. 롤백 토글 왕복 (핵심 미확인 항목 #7)

워크숍 중 문제가 생겼을 때의 탈출구. 토글이 실제로 두 드라이버를 오갈 수
있는지 배포 환경에서 확인한다.

- [ ] SSM에서
      `sudo systemctl set-environment PATHFINDER_DISCOVERY_DRIVER=strands` 후
      `sudo systemctl restart pathfinder-backend`
- [ ] `systemctl is-active pathfinder-backend` → `active`(정상 기동 —
      `strands`는 유효 값이므로 §8의 가드에 걸리지 않는다)
- [ ] 기존 프로젝트(또는 새 프로젝트)에서 대화 턴이 정상 동작(구 Strands
      드라이버 경로) — 응답이 오고, 질문 왕복도 된다
- [ ] 다시 `sudo systemctl set-environment PATHFINDER_DISCOVERY_DRIVER=claude`
      (또는 `unset-environment`로 기본값 복귀) 후 `restart` → 정상 동작
      확인(§3~§4 재확인)
- [ ] **claude로 돌아온 뒤 "이미 transcript가 있는 프로젝트"에서 두 턴 이상
      돌린다.** 이 왕복이 C1을 정면으로 태우는 경로다: strands로 갔다 오는
      동안 claude의 transcript는 디스크에 그대로 남아 있고, 돌아온 프로세스는
      같은 uuid5 id를 다시 파생한다 — 수정 전이라면 여기서 모든 턴이
      `agent turn failed`였다. 응답이 오고 맥락이 이어지면 통과
- [ ] `journalctl -u pathfinder-backend | grep -i "already in use"` → 없음

## 12. 프로토타입 빌드 회귀

Discovery 변경이 빌더를 깨지 않았는지 — `question_file_from_sdk` 통합과
config dir 분리의 영향 범위다. 상세 절차는
`docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md` 참고.

- [ ] 프로토타입 세션을 시작해 첫 질문까지 도달한다
- [ ] 질문 폼이 정상 렌더된다
- [ ] shadcn-design 스킬이 여전히 프로토타입 쪽에서만 동작한다(§1에서 확인한
      `proto-config`/`discovery-config` 경로 분리의 최종 증거 — Discovery
      쪽 대화에서 shadcn 관련 지시가 새어 나오지 않는지도 함께 확인)

---

## 커밋

```bash
git add docs/superpowers/checklists/2026-07-27-discovery-driver-e2e.md
git commit -m "$(cat <<'EOF'
docs: Discovery 드라이버 배포 검증 체크리스트

유닛 테스트로는 부족하다 — 2026-07-26의 미들웨어 Edge 런타임 500이
교훈이다(유닛 테스트 통과, 실제 런타임에서 죽음). 이번엔 그보다 더
근본적인 문제: Task 6-8 리뷰 과정에서 "오프라인 테스트로 못 박을 수
없다"고 명시적으로 남긴 항목이 7개 있다(design.md의 두 미확인 리스크,
Task 6/8 report의 carried-forward 항목들) — setting_sources의 project
스코프가 워크스페이스 CLAUDE.md를 실제로 읽는지, 재시작 후 텍스트 턴
답변을 모델이 이해하는지, disconnect()가 실제 서브프로세스를 걷어내는지,
기동 env 가드가 배포 유닛에서도 붙는지, uuid5 세션 id가 재시작 뒤
--resume을 실제로 잇는지, WebFetch를 포함한 도구명이 전부 한글로
가려지는지, 롤백 토글 왕복.

12개 절: 배선·룰 배치+project 스코프 실측·첫 턴·질문 왕복·새로고침
복원·백엔드 재시작 복원(모델의 실제 이해)·프로젝트 삭제 정리·기동 env
가드·uuid5 재개·도구명 라벨 전수·롤백 왕복·프로토타입 회귀.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```
