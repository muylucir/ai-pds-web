# 프로토타입 생성 기능 — 수동 E2E 체크리스트

Discovery 스펙(`PROTOTYPE-{slug}.md`) → **백엔드 프로세스 안의 빌드 에이전트**(Claude
Agent SDK) → 같은 빌드 디렉토리를 in-place 호스팅 → 경로 프록시 프리뷰 전체 경로를 실
AWS 자원으로 검증한다. 실 Bedrock·실 EC2·실 서브프로세스가 필요해 CI에는 없다(단위
테스트는 `backend/tests/test_proto_*.py`와 `infra/test/`가 fake/Stubber로 커버).

설계는 `docs/superpowers/specs/2026-07-25-prototype-builder-inprocess-design.md`(흡수)와
`docs/superpowers/specs/2026-07-24-prototype-generation-design.md`(원 설계) 참고.

> **2026-07-25 개정**: MicroVM 계층이 제거되면서 (a) VmStack 배포, (b) 이미지 arch 진단,
> (c) VM env 주입, (d) microvm IAM 검증, (i) 고아 VM 정리, (j) auto-suspend 사이클
> 절차는 **수행 불가**가 되어 삭제됐다. 그 자리를 아래 (a)–(d)와 (i)–(m)이 대체한다.

전제: `infra/README.md`를 먼저 훑을 것. 배포는 `npx cdk deploy` 한 번으로 끝난다
(크로스리전 컨텍스트 주입 없음).

---

## (a) 배포 인스턴스에서 번들 바이너리 기동

빌드 에이전트는 SDK wheel이 번들한 Claude Code 바이너리를 서브프로세스로 띄운다.
플랫폼 불일치는 워크숍 중 "session start failed" 502로만 보이므로 먼저 확인한다.

- [ ] SSM으로 접속: `aws ssm start-session --target <InstanceId>`
- [ ] 아키텍처가 x86_64인지 확인: `uname -m` → `x86_64`
- [ ] 번들 바이너리가 뜨는지 확인:
      ```bash
      cd /opt/pathfinder/backend
      .venv/bin/python -c "import claude_agent_sdk, pathlib, subprocess; \
        p=pathlib.Path(claude_agent_sdk.__file__).parent/'_bundled'/'claude'; \
        print(subprocess.run([str(p),'--version'],capture_output=True,text=True).stdout)"
      ```
      → `2.x.x (Claude Code)` 출력. 여기서 실패하면 이후 항목은 전부 무의미하다.
- [ ] 인스턴스 사양 확인: `nproc` → 8, `free -g` total ≈ 31, `df -h /` → 100G

## (b) config 격리 — 호스트 개인 설정이 빌드에 섞이지 않는지

`PATHFINDER_PROTO_CONFIG_DIR`를 비우면 번들 바이너리가 백엔드 실행 유저의 `~/.claude`를
읽는다. 워크숍 결과가 호스트 설정에 의존하게 되므로 **격리를 실물로 확인**한다.

- [ ] systemd env에 값이 들어있는지: `systemctl show pathfinder-backend -p Environment`
      출력에 `PATHFINDER_PROTO_CONFIG_DIR=/opt/pathfinder/proto-config`가 있다
      (앱 트리 안 — 유저 홈이 아니다).
- [ ] 빌드 턴 진행 중 `ps -eo pid,args | grep "[c]laude"`로 뜬 프로세스의 환경을
      확인: `sudo tr '\0' '\n' < /proc/<pid>/environ | grep CLAUDE_CONFIG_DIR` →
      그 격리 경로를 가리킨다.
- [ ] **음성 테스트**(개인 설정이 새지 않는지): 서비스 유저의 홈
      (`getent passwd pathfinder | cut -d: -f6`) 아래
      `.claude/skills/zzz-probe/SKILL.md`를 심고 빌드 턴에서 "사용 가능한 스킬을
      나열해줘"라고 물었을 때 그 스킬이 **보이지 않는다**. 보이면 격리 실패.
- [ ] **양성 테스트**(우리 디렉토리가 실제로 읽히는지): 같은 SKILL.md를
      `/opt/pathfinder/proto-config/skills/zzz-probe/SKILL.md`에 심고
      (**`proto-config/.claude/skills/`가 아니다** — `CLAUDE_CONFIG_DIR`가 곧
      `.claude` 역할이므로 그 아래 `.claude`를 또 만들면 SDK가 무시한다) 같은
      질문에 그 스킬이 **보인다**. 빌더가 `skills="all"`이므로 코드 변경 없이
      켜져야 한다. 두 테스트가 다 통과해야 격리가 증명된다 — 음성만 통과하면
      "경로를 아예 잘못 줘서 아무것도 안 읽히는" 상태와 구별되지 않는다.
      확인 후 프로브 스킬은 지운다.
- [ ] **`skills="all"`의 부작용 확인**: 이 옵션은 SDK가 CLI에
      `--allowedTools Skill`을 붙이게 만든다(확인된 동작). `bypassPermissions`
      아래서 Bash/Write/Edit를 제한하지 않을 것으로 보지만 **검증되지 않았다** —
      빌드 턴에서 에이전트가 실제로 파일을 쓰고 셸을 돌리는지 확인한다. 막히면
      `builder.py`의 `skills="all"`을 제거하거나 명시적 이름 리스트로 바꾼다.
- [ ] transcript 로컬 사본이 우리 경로에 쌓이는지:
      `ls /opt/pathfinder/proto-config/projects/` → 빌드 후 디렉토리가 생긴다.

## (b-2) 서비스가 non-root로 도는지 — 이게 깨지면 빌드가 전부 실패한다

Claude Code는 euid==0에서 `bypassPermissions`를 거부한다(6d21e1f 실측). 그런데
`--version`은 root에서도 성공하므로 **부팅·헬스체크는 모두 정상으로 보이고 첫 빌드
턴에서야 502로 드러난다.** 따라서 반드시 별도로 확인한다.

- [ ] `systemctl show pathfinder-backend -p User` → `User=pathfinder` (root 아님)
- [ ] `ps -o user= -p $(systemctl show -p MainPID --value pathfinder-backend)`
      → `pathfinder`
- [ ] 앱 트리 소유권: `stat -c '%U %G' /opt/pathfinder /opt/pathfinder/protos
      /opt/pathfinder/proto-config` → 셋 다 `pathfinder pathfinder`
- [ ] 빌드 턴 중 `claude` 프로세스도 non-root:
      `ps -eo user,args | grep "[c]laude"` → `pathfinder`
- [ ] **실제 빌드 턴이 성공한다** — 위 네 항목이 통과해도 이것만이 최종 증거다
      (root 문제는 턴에서만 드러나므로).

## (c) 프로세스 자원 실측

스펙 §4의 메모리 예산(claude 1건당 310–577MB)이 이 인스턴스에서도 맞는지 기록한다.

- [ ] 빌드 1건 진행 중: `ps -eo rss,args | grep "[c]laude"` — RSS 합계를 기록
- [ ] `next build` 피크 시점의 `free -m` 값을 기록
- [ ] 세션을 닫고(또는 유휴 만료 후) `ps -eo args | grep -c "[c]laude"` → 0.
      남아 있으면 `disconnect()`가 프로세스를 회수하지 못한 것이다.

## (d) 동시 빌드 상한 (429)

- [ ] 서로 다른 프로토타입 2개의 빌드 세션을 동시에 시작 → 둘 다 202
- [ ] `GET /projects/{pid}/prototypes` 응답의 `active_builds`가 2, `max_builds`가 2
- [ ] 3번째 프로토타입의 세션 시작 → **429**, `detail`이 한국어 안내
      ("다른 팀이 프로토타입을 빌드하고 있습니다 …")
- [ ] 프로토타입 탭에 상한 도달 안내 배너가 보인다
- [ ] **이미 열린 세션의 대화 턴은 막히지 않는다** — 상한에 걸린 상태에서 진행 중인
      세션에 메시지를 보내 정상 응답을 확인(상한은 세션 시작만 게이트한다)
- [ ] 세션 하나를 종료 → `active_builds`가 1로 줄고 3번째 시작이 202

---
## (e) 프로토타입 탭 — 빌드 세션 전체 왕복

- [ ] 프론트 프로젝트의 "프로토타입" 탭(`/projects/{projectId}/prototypes`)을 연다.
      Discovery에서 나온 `PROTOTYPE-{slug}.md` 스펙이 카드로 보이는지 확인
      (`상태: 스펙만 있음`).
- [ ] "빌드 시작" 클릭 → `POST /session` 202 확인(VM 부팅이 없어졌으므로 거의 즉시 —
      claude 서브프로세스 기동 시간만 걸린다). 빌드 패널이 열리고 **첫 턴이 자동으로
      스트리밍**되는지 확인
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

## (f) 재빌드 — 빌드 디렉토리 연속성 확인

번들을 VM에 복원하는 단계는 없어졌다. 빌드 디렉토리가 로컬에 상주하므로 "다시 빌드"는
그 디렉토리를 그대로 이어받는다. transcript까지 이어지는지는 (i)에서 별도로 본다.

- [ ] "빌드 완료" 카드에서 "다시 빌드" 클릭 → 새 세션 시작 → 빌드 패널이 열리고
      `autoStart=true`이므로 **첫 턴이 다시 발화**된다("다시 빌드"도 `handleBuild`를
      그대로 타고, `startSession`이 202면 `autoStart=true`가 된다).
- [ ] 그 첫 턴에서 에이전트가 이전에 만든 파일을 인지하는지 확인(예: "기존 README/코드를
      확인했다"는 취지의 응답, 또는 새 코드를 처음부터 다시 만들지 않는 것).
- [ ] 확실한 확인: SSM에서 `ls <PATHFINDER_PROTO_ROOT>/<pid>/<slug>/prototype/` 이
      이전 빌드 산출물을 그대로 담고 있다(세션 종료가 디렉토리를 지우지 않는다).

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
      대기 → 세션이 자동으로 `close()`되어(빌더 `disconnect()` + 빌드 슬롯 반납) 카드가
      "빌드 완료"로 복귀하는지 확인. **유휴 만료는 이제 맥락을 버리지 않는다** —
      transcript는 S3에 남고 재개 시 이어붙는다((i) 참고).
- [ ] 만료 후 `ps -eo args | grep -c "[c]laude"`가 줄어드는지 확인 — 이 타이머의 목적이
      VM 비용 절감에서 **로컬 메모리 회수**로 바뀌었기 때문이다.
- [ ] 만료 후 `GET /projects/{pid}/prototypes`의 `active_builds`가 감소한다.
      참고: `idle_seconds`는 env로 노출되어 있지 않다(코드 기본값 1800초) — 짧게
      테스트하려면 `backend/pathfinder/app.py`의 `proto_session_factory`가
      `PrototypeSession(...)`을 만드는 지점(`backend/pathfinder/proto/session.py`의
      생성자)에 임시로 `idle_seconds=짧은값`을 넘기는 로컬 패치를 쓰고 **커밋하지
      않는다**.
- [ ] 질문 대기 중(`waiting_input`) 유휴 만료도 같은 방식으로 재현 가능하면 확인 —
      pending future가 소멸하고 세션이 닫히는지(질문 자체는 유실 — 스펙상 수용된
      동작).

## (i) 맥락 재개 — 이 작업의 핵심 검증

VM 시절에는 세션이 닫히면 대화 맥락이 사라졌다. 이제 transcript가 S3로 미러링되고
`resume`으로 이어붙는다. **이 항목이 통과하지 않으면 이 변경의 목적이 달성되지 않은 것**이다.

- [ ] 프로토타입을 빌드해 화면이 나오는 상태까지 진행한다(버튼·색 등 눈에 보이는 요소 포함).
- [ ] 세션을 종료한다("빌드 완료").
- [ ] S3에 transcript가 쌓였는지 확인:
      `aws s3 ls s3://<BUCKET>/projects/<pid>/prototypes/<slug>/transcript/ --recursive`
      → `<session-uuid>/main/00000001.jsonl` 형태의 오브젝트가 1개 이상
- [ ] `prototypes/<slug>/session.json`에 UUID 형태의 `session_id`가 저장돼 있다.
- [ ] **백엔드를 재시작한다**: `sudo systemctl restart pathfinder-backend`
- [ ] 세션을 다시 시작하고 스펙을 언급하지 않은 채 요청한다 —
      예: "방금 만든 화면에서 버튼 색만 바꿔줘".
      에이전트가 **스펙을 다시 읽지 않고** 이전 구현을 참조해 수정하면 통과.
      스펙부터 다시 읽거나 "어떤 화면인가요?"라고 되물으면 resume이 동작하지 않은 것이다.
- [ ] 재개 후 첫 턴이 끝난 뒤 transcript 오브젝트 수가 **늘어났고**(줄지 않았고)
      기존 `00000001.jsonl`이 덮어써지지 않았는지 확인한다(초기 구현의 회귀 지점).

## (j) in-place 호스팅 — npm install이 한 번만

- [ ] 빌드 완료 직후 "호스팅 시작" → 로그 tail에 `npm install`이 **다시 돌지 않는다**
      (빌드 중 이미 설치됨). 설치가 다시 돌면 in-place가 아니라 재다운로드 경로다.
- [ ] 빌드 세션이 **살아있는 동안** 호스팅 시작 → **409** + 한국어 안내
      (진행 중인 빌드를 지우지 않기 위한 가드)
- [ ] 호스팅 중 빌드 디렉토리가 그대로인지: SSM에서
      `ls <PATHFINDER_PROTO_ROOT>/<pid>/<slug>/` → `node_modules`와 소스가 함께 있다

## (k) 바이너리 에셋 무손실

- [ ] 이미지(png/jpg)나 폰트를 포함한 프로토타입을 빌드한다.
- [ ] 프리뷰에서 그 이미지가 정상 렌더된다(깨진 이미지 아이콘이 아니다).
- [ ] `.../archive` zip을 내려 이미지 파일을 열어본다 — 원본과 동일하게 열린다
      (U+FFFD 손상이면 이미지 뷰어가 거부한다).

## (l) 아티팩트 zip — 개발팀 인계

- [ ] 카드의 "다운로드" 클릭 → zip 다운로드, 파일명에 slug가 들어간다
      (한글 slug도 저장 실패 없이 받아진다)
- [ ] zip 안에 `README`와 `package.json`이 있다.
- [ ] zip 안에 `node_modules/`, `.next/`, `.git/`, `.proto-host.log`, `.proto-host.pid`가
      **없다**.
- [ ] zip 안에 `survey/`(익명 응답자 원문)와 `transcript/`(빌드 대화)가 **없다** —
      외부 개발팀에 넘기는 파일이므로 이 항목은 프라이버시 요구사항이다.
- [ ] 번들이 아직 없는 프로토타입의 `.../archive` → 404

## (m) 고아 호스팅 프로세스 정리 (구 고아 VM 스윕의 대체물)

빌드/호스팅 자식 프로세스는 이제 백엔드의 자식이다. 백엔드가 강제 종료되면 남는다.

- [ ] 호스팅을 시작한 뒤 백엔드를 강제 종료: `sudo kill -9 $(systemctl show -p MainPID
      --value pathfinder-backend)`
- [ ] `ps -eo args | grep "[n]pm run"`으로 자식이 살아있는 것을 확인(정상 — 이게 문제 상황)
- [ ] 백엔드를 재기동: `sudo systemctl start pathfinder-backend`
- [ ] 로그에 `swept N orphan prototype hosting process(es)`가 찍힌다:
      `journalctl -u pathfinder-backend -n 100 | grep orphan`
- [ ] 그 포트가 해제됐다: `ss -ltnp | grep 400` → 해당 포트 없음
- [ ] `.proto-host.pid` 파일이 정리됐다

## (n) 업로드 키 — 동시 업로드가 서로를 덮지 않는지

- [ ] 같은 파일명(예: `요구사항.md`)을 두 번 업로드 → 반환 경로가 서로 다른
      `uploads/{uuid8}/...` 이고 **두 파일 모두** 문서 패널에서 열린다
- [ ] `요구사항.pdf`와 `요구사항.xlsx`를 각각 업로드 → 키에 원본 확장자가 남아
      어느 쪽이 어느 원본인지 구분된다
- [ ] 첨부 칩에는 uuid 디렉토리가 노출되지 않고 원본 파일명만 보인다

## 검증 설문 (2026-07-25 추가)

- [ ] 프로토타입 탭에서 "질문 생성" → 201, 6~10문항 생성 확인
- [ ] `aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md` 가
      문서 패널에서 열리는지 확인
- [ ] 공개 링크를 **로그아웃 상태(다른 브라우저/시크릿 창)** 로 열어 문항이 보이는지 확인
- [ ] 공개 응답 본문에 project_id·slug가 없는지 DevTools Network에서 확인
- [ ] 3종 문항(scale/choice/text) 응답 제출 → 완료 화면, 재제출 폼 미노출
- [ ] 대시보드 새로고침 → 응답 수·평균·선택 분포·자유응답 샘플 반영
- [ ] 여러 건 제출 후 S3에 `responses/{uuid}.json` 개수 일치 확인
- [ ] `rollup.json` 을 S3에서 수동 삭제 후 대시보드 새로고침 → 수치 정상 재구축
- [ ] 필수 문항 미응답 제출 → 400, 없는 선택지 전송 → 400 (DevTools로 직접 POST)
- [ ] "설문 마감" → 공개 링크 재방문 시 410 "마감되었습니다" 화면
- [ ] CSV 내보내기 → 문항 헤더·한글 정상, Step 6 종합에 붙여넣기 가능
- [ ] 마감 후 "새 설문 생성" → 이전 응답이 `archive/{closed_at}/` 로 이동하고
      새 설문 집계에 섞이지 않는지 확인
