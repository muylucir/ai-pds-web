# 프로토타입 빌더 백엔드 흡수 — MicroVM 제거 + 맥락 지속성 설계

날짜: 2026-07-25
상태: 설계 확정 (사용자 승인)

## 1. 배경과 목표

프로토타입 빌드는 지금 도쿄 Lambda MicroVM 안의 하네스 서버(`harness/`)에서
Claude Agent SDK가 수행하고, 백엔드는 VM 부팅/정지와 HTTP 중계(`HarnessClient`)를
담당한다. 이 구조를 제거하고 **빌드 에이전트를 백엔드 프로세스 안에서 직접
실행**한다.

동기(사용자 확인 — 두 통증이 같은 뿌리):

- **대화 맥락 유실**: `session.py`의 재빌드 경로가 "새 `ClaudeSDKClient`, resume
  없음"으로 명시돼 있다. 세션이 닫히면 클라이언트가 죽으므로, 며칠 뒤 "이 버튼 색
  바꿔줘"를 하려면 에이전트가 코드·결정 히스토리를 전혀 모른다. 매번 스펙부터
  다시 읽힘.
- **운영 복잡도**: 리전이 갈리고(서울 백엔드 / 도쿄 VM), CDK 이미지 재빌드·토큰
  민팅·고아 VM 스윕·부팅 폴링(최대 120초)이 모두 관리 대상.

이 설계는 2026-07-21의 `microvm-removal-inprocess-agent` (Discovery 에이전트를
VM에서 백엔드로 이전)와 같은 형태의 이동이며, 그때 확립한 패턴(S3 = source of
truth, 로컬은 휘발)을 재사용한다.

명시적 결정(사용자):

- **격리 포기를 수용** — `ProtoHost`가 이미 생성 코드를 인스턴스 롤로 서브프로세스
  실행한다(2026-07-24 스펙 §5의 명시적 트레이드오프). 빌드 에이전트를 같은 상자에
  넣는 것은 새 리스크가 아니라는 판단.
- **맥락은 재시작도 넘어선다** — SDK의 `session_store`(S3 어댑터) + `resume`으로
  transcript를 영속화. 인메모리 클라이언트만으로는 백엔드 재배포 때 날아간다.
- **빌드 디렉토리를 그대로 호스팅** — S3는 백업 역할. `npm install`이 한 번만
  돌고, 바이너리 에셋이 정상 경로에서 깨지지 않는다.
- **VM 자산은 전부 삭제** — 롤백 경로 없음, git이 유일한 복구 수단(2026-07-21과
  동일 방침).
- **배포 대상은 기존 워크숍 EC2** — `PathfinderHostingStack`이 배포하는 단일
  인스턴스. 프로토타입 전용 인스턴스를 신설하지 않는다. 대신 그 인스턴스를
  **m7i.2xlarge(x86_64) / EBS 100GB**로 상향한다. Graviton(arm64)은 쓰지 않는다.
- **동시 빌드 상한 2건**(환경변수 조정) — 초과 시 429 안내.
- **업로드 경로 개편을 같은 스펙에 포함** — 동시 사용 시 조용한 덮어쓰기가 실재.
- **S3 바이너리 안전 경로 추가** — 번들 백업/복원에서 이미지·폰트가 깨지는 문제.

## 2. 아키텍처

### 이전 (harness/ → backend/pathfinder/proto/)

```
[이전]  백엔드 ──HTTP(민팅 토큰)──▶ 도쿄 MicroVM ──in-process──▶ ClaudeSDKClient
[이후]  백엔드 ──────────────────in-process─────────────────▶ ClaudeSDKClient
```

```
프론트 [프로토타입 탭]  ── SSE (AgentEvent 계약 무변경) ──┐
                                                        ▼
백엔드 FastAPI (워크숍 EC2, 서울) ── 단일 프로세스 ──────────────────
  ├─ StrandsDriver          Discovery 에이전트 (기존, 무변경)
  ├─ PrototypeBuilder       빌드 에이전트 = ClaudeSDKClient (신규, in-process)
  │     · cwd = PATHFINDER_PROTO_ROOT/{pid}/{slug}/
  │     · session_store = S3SessionStore → resume=<uuid>
  │     · can_use_tool(AskUserQuestion) / PostToolUse 훅 / interrupt
  ├─ ProtoHost              같은 디렉토리를 in-place 호스팅 (npm install 1회)
  └─ /proto/{pid}/{slug}/*  리버스 프록시 (무변경)
```

| 원본 | 이동 후 | 비고 |
|---|---|---|
| `harness/sdk_driver.py` | `backend/pathfinder/proto/builder.py` | 거의 그대로 이식. interrupt·AskUserQuestion 가로채기·PostToolUse 경로 가드가 이미 여기 구현돼 있다 |
| `harness/globmatch.py` | 삭제 | `pathfinder/globmatch.py`에 동일본 존재 |
| `harness/events.py` | 삭제 | `pathfinder.models.AgentEvent`로 통합 |
| `harness/app.py`, `serve.py`, `hooks.py`, `pathsafe.py`, `Dockerfile`, `requirements.txt`, `tests/` | 삭제 | HTTP 계약 자체가 소멸 |

### 삭제

- `harness/` 디렉토리 전체 (`builder.py` 이식 후)
- `backend/pathfinder/proto/vm.py` — `LambdaMicroVMController`, `BootSpec`,
  `VMHandle`, `mint_harness_token`, `FakeMicroVMController`
- `backend/pathfinder/proto/harness_client.py` — `HarnessClient`
- `backend/pathfinder/app.py`: `_cleanup_orphan_vms`, `_proto_http_client`,
  `proto_session_factory`의 VM 조립부
- `infra/lib/pathfinder-vm-stack.ts`, `infra/package-harness.sh`
- `infra/lib/backend-permissions.ts`의 `microvmControlStatements`
- env 4개: `PATHFINDER_VM_REGION`, `PATHFINDER_VM_IMAGE_ID`,
  `PATHFINDER_VM_ROLE_ARN` (+ `HostingStackProps`의 대응 필드)
- 백엔드 롤의 `lambda-microvms` 권한

### 살아남는 것

- `proto/session.py`의 오케스트레이션 골격 — 상태 기계(`SessionStatus`), 유휴
  타이머, 질문 interrupt id 소유권, 첫 턴 자동 발화(`first_prompt`). VM 부팅/파일
  push 단계만 빠진다.
- `proto/host.py` — in-place 호스팅으로 개조(§5)
- `routes/prototypes.py` — REST/SSE 표면 무변경 + zip 라우트 추가
- 프론트엔드 전체 — API 계약 무변경 (다운로드 버튼·대기 표시만 추가)

## 3. 맥락 지속성 — S3SessionStore + resume

SDK 0.2.126 실물 확인 사항:

- `ClaudeSDKClient`가 `session_store` + `resume`을 지원(`client.py:120-133,
  243-255`). `can_use_tool`을 쓰는 스트리밍 경로와 양립한다.
- `resume` 값은 **반드시 유효한 UUID**(`_internal/session_resume.py:151`).
  `f"{pid}-{slug}"` 같은 문자열은 거부된다.
- resume 시 SDK가 스토어에서 `load()` → 임시 `CLAUDE_CONFIG_DIR`에 JSONL 재구성
  → 서브프로세스가 그것을 읽는다. 정리는 SDK가 담당(`client.py:142`).
- `session_store` + `enable_file_checkpointing`은 동시 사용
  불가(`session_store_validation.py:39`). 체크포인팅은 쓰지 않으므로 무관.
- `continue_conversation`을 쓰면 스토어가 `list_sessions()`를 구현해야
  한다(`session_store_validation.py:30-37`). 우리는 `resume`을 명시하므로 불필요.

### 설계

| 항목 | 값 |
|---|---|
| `project_key` | `f"{pid}/{slug}"` — 프로토타입별 완전 격리 |
| `session_id` | UUID4 생성. `prototypes/{slug}/session.json`에 영속 |
| S3 키 | `projects/{pid}/prototypes/{slug}/transcript/{seq}.jsonl` |
| flush 모드 | `batched`(기본) — 턴당 1회 |
| 구현 메서드 | `append` / `load` / `list_subkeys` |
| 미구현 | `list_sessions`, `list_session_summaries`, `delete` — Protocol 기본값이 `NotImplementedError`이고 우리 경로에서 호출되지 않는다 |

`SessionStoreEntry`는 불투명 JSON 블롭으로 취급한다(`types.py:1388-1401` — 라운드
트립만 보장하면 되고 byte-equal은 불필요). `uuid` 필드가 있는 엔트리는 멱등 키로
쓴다.

### 세션 시작 흐름

```
POST /projects/{pid}/prototypes/{slug}/session
  1. 동시 빌드 세마포어 획득 시도 → 실패 시 429
  2. S3에서 PROTOTYPE-{slug}.md 확인 (없으면 404)
  3. prototypes/{slug}/session.json 읽기
       있으면 → resume=<uuid>          (맥락 이어받기)
       없으면 → uuid4 생성 후 저장     (새 세션)
  4. 빌드 디렉토리 확인/생성 (PATHFINDER_PROTO_ROOT/{pid}/{slug}/)
       비어 있고 S3 백업이 있으면 → 복원 (§6 바이너리 안전 경로)
  5. ClaudeSDKClient 생성 + connect() → claude 서브프로세스 기동
  6. status="ready"
```

부팅 폴링·파일 push가 사라지므로 이 경로는 대략 프로세스 기동 시간만 걸린다.

**살리지 않는 것**: 프롬프트가 열린 채 대기 중인 질문(pending interrupt).
`can_use_tool`의 pending future는 프로세스 메모리에만 있어 재시작 시 소멸한다 —
2026-07-24 스펙 §6과 동일한 수용 사항이며, 재개 후 에이전트가 transcript를 보고
다시 묻는다.

## 4. 자원 · 동시성 (워크숍 EC2)

**대상**: `PathfinderHostingStack`의 단일 인스턴스. 프론트(Next.js) + 백엔드
(uvicorn) + nginx가 이미 여기서 돈다.

| 항목 | 현재 | 변경 후 |
|---|---|---|
| 인스턴스 타입 | t4g.medium (2 vCPU / 4GB) | **m7i.2xlarge (8 vCPU / 32GB)** |
| EBS | 20GB | **100GB** |
| 아키텍처 | arm64 (Graviton) | **x86_64** |

### 아키텍처 전환 (arm64 → x86_64)

사용자 결정으로 Graviton을 쓰지 않는다. 부수 효과로 아키텍처 리스크가 사라진다:

- SDK 번들 바이너리가 x86-64 ELF이고(`_bundled/claude` — `file`로 확인), 이
  개발 환경에서 이미 검증된 바이너리와 동일한 아키텍처가 된다. arm64였다면
  `manylinux_2_17_aarch64` wheel(84.2MB, 배포 확인됨)로 동작하되 실기 검증이
  별도로 필요했다.
- 프로토타입이 설치하는 네이티브 npm 모듈(sharp, esbuild 등)도 x86_64 prebuilt를
  받으므로 소스 빌드 폴백 위험이 줄어든다.

CDK 변경은 두 줄이다: `instanceType` → `M7I`/`XLARGE2`, `machineImage`의
`cpuType: ARM_64` 제거(AL2023 기본이 x86_64). `user-data.ts`는 `dnf` 패키지명만
쓰고 아키텍처 분기가 없어 **무변경**이다.

### 프로세스 모델

`ClaudeSDKClient` 1개 = `claude` 서브프로세스 1개
(`_internal/transport/subprocess_cli.py:733`의 `anyio.open_process`). 세션당
클라이언트 1개를 유지하므로 **세션 N개 = 프로세스 N개**.

```
uvicorn (백엔드)
├── claude  (A팀 세션, cwd=PROTO_ROOT/projA/slugA)
│   └── bash/npm 등 에이전트가 띄우는 자식
├── claude  (B팀 세션, cwd=PROTO_ROOT/projB/slugB)
├── npm run start  (A 호스팅, port 4001)
└── npm run start  (B 호스팅, port 4002)
```

프로세스가 갈리고 `cwd`가 갈리므로 팀 간 파일 간섭이 없고, 빌드 CPU가 백엔드
이벤트 루프를 막지 않는다(stdout을 읽기만 함).

### 메모리 예산 (32GB, 동시 2건 피크)

`claude` 프로세스 RSS는 실측 310–577MB (개발 박스 x86_64 기준 — 배포 대상도
x86_64이므로 같은 아키텍처의 실측치다).

| 항목 | 메모리 |
|---|---|
| 프론트 + 백엔드 + nginx | ~1G |
| `claude` × 2 | 0.6–1.2G |
| `next build` × 2 (에이전트의 자식) | 2–4G |
| `npm run start` × 2 (호스팅 상주) | 0.4–1G |
| **합계** | **4–7.2G** — 32G에 큰 여유 |

8 vCPU / 32GB는 동시 2건을 훨씬 넘어설 여유가 있다. 그래도 기본 상한은 2로 두고
(`PATHFINDER_PROTO_MAX_CONCURRENT`) 워크숍 운영 중 실측을 보며 올린다 — 병목이
메모리가 아니라 `next build`가 vCPU를 나눠 쓰는 쪽이라, 값을 정하는 근거는 실제
빌드 체감 속도여야 한다.

### 동시 상한

`PATHFINDER_PROTO_MAX_CONCURRENT`(기본 2), CDK userData로 주입. 전역
세마포어이며 **세션 시작 시점만 게이트한다** — 이미 열린 세션의 대화 턴은 막지
않는다. 초과 시 429 + `"다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시
시도해 주세요"`. 상태 API가 `active_builds`/`max_builds`를 노출해 카드에 대기
상황을 표시한다. 상한이 코드에 박히지 않으므로 인스턴스를 키우면 값만 올린다.

### 디스크

EBS 100GB. 프로토타입당 실측 23MB(`node_modules` 포함)이므로 수십 개도 여유.
자동 정리 정책은 두지 않고 모니터링만 한다.

### 유휴 타이머의 의미 변화

기존 30분 타이머는 VM 비용을 아끼는 장치였으나, 이제 **로컬 메모리를 회수하는
장치**다(세션이 유휴로 열려만 있어도 `claude` 프로세스가 300–500MB 상주). 만료 시
하는 일이 "VM stop" → "`client.disconnect()` + transcript 플러시 확인 + 세마포어
반납"으로 바뀐다.

**VM 시절과 달리 유휴 만료가 맥락 손실을 뜻하지 않는다** — transcript가 S3에
있으므로 재개 시 resume된다.

## 5. ProtoHost — in-place 호스팅 개조

빌드 디렉토리와 호스팅 디렉토리가 같아지면서, 현재 코드의 세 지점이 실제 버그가
된다. 함께 고친다.

1. **`start()`의 `rmtree`** (`host.py:169-172`) — 지금은 디렉토리를 지우고 S3에서
   재다운로드한다. in-place로 바뀌면 **진행 중인 빌드를 통째로 삭제**한다.
   → `rmtree` 제거. 라이브 빌드 세션이 있는 `(pid, slug)`의 호스팅 시작은 409.
   디렉토리가 비어 있을 때만 S3 백업에서 복원한다.
2. **포트 스캔 레이스** (`host.py:51-66`) — bind로 탐색한 뒤 소켓을 닫고 나서
   서브프로세스를 띄운다. 동시 호스팅이 같은 포트를 집을 수 있다.
   → 레지스트리에 포트 예약을 기록하고, 스캔이 예약된 포트를 건너뛴다.
3. **고아 프로세스** — 없어지는 고아 VM 스윕의 대체물이 필요하다. 백엔드가
   SIGKILL로 죽으면 자식 `claude`/`npm`이 살아남아 CPU와 포트를 계속 문다.
   → 자식을 `start_new_session=True`로 띄우고, 기동 시
   `PATHFINDER_PROTO_ROOT/**/.proto-host.pid` 기반 스윕 + lifespan 종료 시 프로세스
   그룹 kill.

`npm install`은 빌드 중 에이전트가 이미 실행했으므로 호스팅이 재사용한다. 바이너리
에셋도 S3 왕복을 타지 않아 정상 경로에서 깨지지 않는다.

## 6. S3 바이너리 안전 경로

`S3Store`는 텍스트 전용이고(`s3store.py:39` `.decode("utf-8")`),
`harness/app.py:83`은 lossy 디코드였다. 그래서 지금 번들 왕복은 이미지·폰트를
U+FFFD로 깨뜨린다.

in-place 호스팅으로 **정상 경로에서는 S3 왕복이 빠져 자동 해결**된다. 남는 것은
"재배포로 로컬이 날아간 뒤 백업에서 복원"하는 경로이며, 그것도 닫는다:

- `S3Store`에 `get_bytes` / `put_bytes` 추가. `S3StoreLike` Protocol에도 반영.
- **번들 백업/복원과 zip 생성만** 이 경로를 사용한다. 기존 텍스트 API와 Discovery
  경로는 무변경.

## 7. 프로토타입 아티팩트 zip 다운로드

개발팀 인계용. `artifacts.py:57-74`의 `download_artifacts_archive`와 같은 형태.

```
GET /projects/{pid}/prototypes/{slug}/archive  →  {slug}-prototype.zip
```

| 항목 | 결정 |
|---|---|
| 소스 | **로컬 빌드 디렉토리**(권위 사본). 없으면 `prototypes/{slug}/bundle/` S3 백업으로 폴백 |
| 범위 | 프로토타입 소스 **only**. `prototypes/{slug}/survey/**`(익명 응답 원문)와 `transcript/**`는 **제외** — 같은 프리픽스 아래 있으나 인계 대상이 아니다 |
| 제외 경로 | `node_modules/`, `.next/`, `.git/`, `.proto-host.log`, `.proto-host.pid` — `session.py:32`의 `_EXCLUDED_SEGMENTS` 재사용 |
| 인코딩 | **바이너리 안전** — `zipfile.writestr`에 bytes를 직접 넣는다(§6). 기존 aiplc-docs zip은 텍스트 경로라 무변경 |
| 파일명 헤더 | `artifacts.py:14-22`의 `_content_disposition` 재사용 — 한글 slug의 latin-1 헤더 에러를 이미 해결해 둔 코드 |
| 버퍼링 | `io.BytesIO`. `node_modules` 제외 후 수 MB이므로 기존 zip과 동일 방식 |
| 상태 조건 | 번들이 없으면 404. 빌드 진행 중에도 허용(그 시점 스냅샷) |
| UI | `PrototypeCard`의 액션 영역에 "다운로드" 버튼 — `설문` 버튼과 같은 패턴(옵셔널 `onDownload?` 콜백 prop + `SECONDARY_BTN`). 번들이 있을 때(`built`/`running`)만 노출하므로 상태 분기 안에 둔다(`설문`은 상태 무관이라 분기 밖에 있다) |

첫 턴 발화가 README 작성을 지시하므로(`session.py:277-278`) README와
`package.json`이 반드시 포함된다.

## 8. Claude Code 설정 격리

### 사실관계

| 항목 | 확인 결과 |
|---|---|
| 실행 엔진 | Claude Agent SDK는 **Claude Code 바이너리를 감싼 래퍼**다. `_bundled/claude`를 서브프로세스로 띄운다 |
| 백엔드 venv | 현재 SDK 없음 → **신규 의존성** `claude-agent-sdk==0.2.126` (`pyproject.toml`). x86_64 wheel의 번들 바이너리는 273MB |
| 바이너리 해석 순서 | 번들 우선(`subprocess_cli.py:153`) → PATH 폴백(`:159`). 버전 핀이 requirements 한 곳으로 모인다 |

"claude code를 걷어냈다"는 것은 **운영 방식**(npm 글로벌 설치, `claude -p` 직접
구동, `--continue` 관리)에 대해 맞고, **실행 엔진**은 여전히 Claude Code다.

### 문제

번들 바이너리도 평범한 Claude Code라서 뜰 때 config 디렉토리를 읽는다:
`CLAUDE_CONFIG_DIR`가 있으면 그 경로, 없으면 `~/.claude`
(`_internal/sessions.py:122-128`). VM 안에서는 `harness` 유저의 홈이 비어 우연히
안전했다. 워크숍 EC2에서 백엔드 유저의 홈에 개인 스킬/에이전트/CLAUDE.md가 있으면
그것이 빌드 에이전트 컨텍스트에 섞이고, 결과가 호스트 설정에 따라 달라져 재현이
안 된다.

### 결정

```python
ClaudeAgentOptions(
    cwd=build_dir,
    env={
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CONFIG_DIR": str(PATHFINDER_PROTO_CONFIG_DIR),
    },
    setting_sources=["user", "project"],
    ...
)
```

`setting_sources=[]`(전부 끄기)보다 `CLAUDE_CONFIG_DIR` 지정이 낫다 — 홈을
갈아끼우는 방식이라 (a) 개인 설정이 차단되고, (b) transcript 로컬 사본이 Pathfinder
소유 경로에 쌓여 프로젝트 삭제 시 함께 지울 수 있고, (c) 나중에 우리가 원하는
스킬/서브에이전트를 넣을 자리가 남는다.

```
PATHFINDER_PROTO_CONFIG_DIR/     (기본 ~/pathfinder-proto-config)
  settings.json        ← 필요 시
  skills/              ← 프로토타입 빌드용 스킬 (1차 스코프에서는 비움)
  agents/              ← 서브에이전트 정의 (또는 코드로 agents= 사용)
  projects/            ← transcript 로컬 사본 (S3SessionStore가 미러링)
```

`setting_sources`를 `["user", "project"]`로 두는 이유: 여기서 "user"는 이제 우리
전용 디렉토리를 뜻하므로 안전하고, `skills=[...]`를 켤 때 SDK가 요구하는 소스가
이미 열려 있다(`subprocess_cli.py`의 `_apply_skills_defaults`가 `setting_sources`
미설정 시 이 값을 기본으로 넣는다). "project"는 cwd(빌드 디렉토리)의 `.claude/`이므로 에이전트가
스스로 만든 설정도 살아난다.

**확장 지점**: Skill은 `skills=[...] | "all"`(`types.py:1999` — 이것만 설정하면
`allowed_tools`·`setting_sources`를 SDK가 자동 처리), 서브에이전트는
`agents={name: AgentDefinition(...)}`(`types.py:1981`, 파일 없이 코드로 정의).
**1차 스코프에서는 둘 다 켜지 않고 자리만 확보한다** — 무엇이 프로토타입 빌드에
유용한지는 별도 판단 사항.

주의: `skills`는 컨텍스트 필터이지 샌드박스가 아니다(`types.py:2013`). 목록에서
숨겨도 파일은 디스크에 남고 Read/Bash로 접근 가능하다. `bypassPermissions`로 도는
이상 실효적 방어는 `CLAUDE_CONFIG_DIR`로 **애초에 로드하지 않는 것**이다.

## 9. 업로드 경로 개편

### 현재 문제

- **(a)** 충돌 검사 글롭이 `uploads/*`인데(`uploads.py:26`),
  `globmatch.py:17-18`은 `**`가 없으면 순수 `fnmatch`로 떨어진다. `fnmatch`의 `*`는
  `/`를 넘으므로 지금은 우연히 동작하지만 의도가 불명확하다.
- **(b)** `safe_name`이 확장자를 항상 `.md`로 강제한다(`parsers/uploads.py:79-85`).
  `요구사항.pdf`와 `요구사항.xlsx`가 같은 키를 노리고, `-2` 접미사가 붙어도
  **원본이 무엇이었는지 구분할 근거가 사라진다**.
- **(c)** `list_files` → `safe_name` → `write_file` 사이에 락이 없다. 두 사람이 같은
  이름을 동시에 올리면 둘 다 비어 있다고 판단하고 **나중 쓰기가 앞선 파일을 조용히
  삭제한다**. `S3Store.put`은 무조건 `put_object`라 조건부 쓰기도 없다.

프로젝트 ID는 이미 격리돼 있다 — `app.py:38`의
`S3Store(prefix=f"projects/{project_id}/")`가 스토어를 프로젝트별로
네임스페이스하므로 실제 키는 `projects/{pid}/uploads/...`다. 별도의 `<pid>` 경로
세그먼트는 불필요하며, 이 레이아웃에 `projects.py`의 삭제·매니페스트 로직이 묶여
있어 바꾸면 파급이 크다.

### 새 키 형태

```
uploads/{uuid8}/요구사항.pdf.md
```

- `uuid8` 디렉토리가 유일하므로 **충돌 검사 자체가 불필요**해지고 read-to-write 창이
  사라진다. 따라서 **별도 락을 두지 않는다** — 직렬화할 공유 상태가 없다.
- 원본명·원본확장자를 키에 보존한다. `.md` 접미사는 유지 — 내용은 변환된
  마크다운이고, 프론트/에이전트/룰이 `.md`를 기대한다.
- 원본 bytes는 저장하지 않는다(에이전트는 텍스트 도구뿐 — 기존 정책 유지).
- 방어 심층화로 `put`에 `IfNoneMatch="*"` (boto3 1.43.50 지원 확인). uuid 충돌이라는
  사실상 불가능한 경우도 조용히 덮어쓰지 않고 실패한다.
- `AttachmentChips.tsx:18`이 `uploads/` 접두만 벗기고 있으므로 `uploads/{uuid8}/`
  전체를 벗겨 원본 파일명만 표시하도록 고친다.
- 첨부는 프롬프트 문자열로 에이전트에 전달된다
  (`workspace/page.tsx:100`) — 경로만 바뀌고 형식은 무변경.

### 기존 데이터

실측 8건, 모두 프로젝트당 1개(`projects/{pid}/uploads/*.md`). 구 경로는 **읽기만
계속 지원**하고 마이그레이션 스크립트는 만들지 않는다. `runner.py:36`의
`_RESTORE_PREFIXES`가 `uploads/`를 프리픽스로 훑으므로 신·구 경로가 함께 복원된다.

## 10. 인프라 변경

- `pathfinder-hosting-stack.ts`: `instanceType` → m7i.2xlarge(`M7I`/`XLARGE2`),
  `machineImage`의 `cpuType: ARM_64` 제거(x86_64 기본), EBS 20→100GB
- `microvmControlStatements` 호출 제거(`:85-87`) + `backend-permissions.ts`에서 함수
  삭제
- `HostingStackProps`의 `vmImageId`/`vmRoleArn`/`vmRegion` 삭제,
  `bin/app.ts:26-32`의 컨텍스트 주입 삭제
- `user-data.ts`: VM env 3개 제거, `PATHFINDER_PROTO_MAX_CONCURRENT`(기본 2)·
  `PATHFINDER_PROTO_CONFIG_DIR` 추가
- `bin/app.ts`에서 `PathfinderVmStack` 삭제 + `lib/pathfinder-vm-stack.ts` 삭제 +
  `package-harness.sh` 삭제
- 배포 후 도쿄 스택 수동 정리:
  `npx cdk destroy PathfinderVmStack --region ap-northeast-1`
- `infra/test/`의 VM 스택 assertion 삭제, 인스턴스 타입/EBS assertion 갱신

배포 절차가 단순해진다 — 크로스리전 컨텍스트 주입
(`-c vmImageId=... -c vmRoleArn=...`)이 사라져 `npx cdk deploy`만 남는다.

### env 요약

| 변수 | 기본값 | 상태 |
|---|---|---|
| `PATHFINDER_VM_REGION` | — | **삭제** |
| `PATHFINDER_VM_IMAGE_ID` | — | **삭제** |
| `PATHFINDER_VM_ROLE_ARN` | — | **삭제** |
| `PATHFINDER_PROTO_ROOT` | `~/pathfinder-protos` | 유지 (빌드 + 호스팅 공용 루트로 의미 확장) |
| `PATHFINDER_PROTO_MAX_CONCURRENT` | `2` | **신규** |
| `PATHFINDER_PROTO_CONFIG_DIR` | `~/pathfinder-proto-config` | **신규** |

## 11. 에러 처리

| 상황 | 처리 |
|---|---|
| 동시 빌드 상한 초과 | 429 + 안내. 카드에 대기 표시 |
| 빌드 에이전트 턴 실패 | sanitize된 `error` 이벤트(상세는 서버 로그만). 세션 유지·재시도 (무변경) |
| resume 실패 (transcript 손상/부재) | 경고 로그 + **새 세션으로 시작**. 맥락은 잃지만 빌드는 가능 — fail-soft |
| `session_store.append` 실패 | SDK가 3회 재시도 후 `MirrorErrorMessage`. 턴은 계속 — transcript 미러링 실패가 빌드를 죽이지 않는다 |
| `claude` 프로세스 조기 사망 | 스트림에서 예외 → 세션 `failed`. 재시작은 resume으로 맥락 복구 |
| 사용자 중단 | `client.interrupt()` → 버퍼 드레인 → `status:"interrupted"` + `done` (무변경) |
| 유휴 30분 | `disconnect()` + transcript 플러시 + 세마포어 반납 → 카드 `built` 복귀. 맥락 보존 |
| 백엔드 재시작 | 인메모리 세션 소멸. 빌드 디렉토리·transcript 상주 → 재개 시 resume. 호스팅 프로세스는 pid 스윕 후 수동 재기동 |
| 진행 중 빌드에 호스팅 시작 | 409 (신규 — rmtree 삭제 사고 방지) |
| npm install/빌드 실패 | 로그 tail을 상태 API로 노출 (무변경) |
| 업로드 키 충돌 (`IfNoneMatch` 실패) | 409 — 재시도 가능 |
| zip: 번들 없음 | 404 |

## 12. 테스트

- **빌드 드라이버**: `harness/tests`의 `sdk_driver` 테스트를 `backend/tests`로 이식.
  기존 `client_factory` 주입점을 그대로 유지해 fake SDK client로 AWS 없이
  검증(메시지 → AgentEvent 번역, interrupt 후 버퍼 드레인, AskUserQuestion 왕복,
  PostToolUse 경로 가드). `harness/tests`의 app/serve/hooks 테스트는 폐기.
- **S3SessionStore**: `moto[s3]`로 append → load 라운드트립, `list_subkeys`,
  session_id UUID 형식 검증, resume 실패 시 새 세션 fail-soft.
- **세마포어**: 상한 초과 429, 세션 종료 시 반납, 유휴 만료 시 반납.
- **ProtoHost**: in-place 시작(rmtree 없음), 라이브 세션 있을 때 409, 포트 예약,
  pid 스윕.
- **S3Store bytes**: `put_bytes`/`get_bytes` 라운드트립으로 바이너리 무손실.
- **zip 라우트**: 제외 경로 반영, `survey/`·`transcript/` 미포함, 한글 slug
  Content-Disposition, 번들 없으면 404.
- **업로드**: uuid8 키 형태, 원본명·원본확장자 보존, `IfNoneMatch` 거부 시 409,
  구 경로 읽기 호환.
- **인프라**: VM 스택 assertion 삭제, 인스턴스 타입/EBS assertion 갱신,
  `microvmControlStatements` 부재 확인.
- **프론트**: 다운로드 버튼 상태별 노출, 카드 대기 표시. API 계약 무변경이므로 기존
  테스트는 영향 없음.
- **e2e**: 수동 체크리스트 갱신 — VM 절차 삭제, **실기 검증 항목 추가**(SDK 번들
  바이너리 기동, 프로토타입 네이티브 npm 모듈 설치, `claude` 프로세스 RSS 실측 및
  동시 2건 피크 관측), 맥락 재개 시나리오(세션 닫고 백엔드 재시작 후 이전 결정을
  에이전트가 참조하는지) 추가.

## 13. 직전 커밋(validation survey) 영향 분석

세션 시작 시점 이후 12개 커밋으로 validation survey가 구현됐다
(`e3caa01..fe66f98`). 영향 검토 결과:

| 항목 | 판정 |
|---|---|
| **§7 zip 범위** | **영향 있음.** Survey가 `prototypes/{slug}/survey/**`를 쓴다(`survey/store.py:4-7`) — zip이 담으려던 트리 안이다. 익명 응답 원문이 인계 zip에 섞이면 안 되므로 §7이 소스를 `bundle/`+로컬 빌드 디렉토리로 명시 한정하고 `survey/`·`transcript/`를 제외한다 |
| **§10 삭제 범위** | 충돌 없음. Survey는 `bin/app.ts`·`user-data.ts`를 건드리지 않았다. 단 `app.py`에 survey 배선 33줄이 `_cleanup_orphan_vms` 바로 위에 추가됐으므로 삭제 시 그 블록을 건드리지 않도록 주의 |
| **§4 자원 예산** | 무영향. `questionnaire_agent_factory`(`app.py`)는 Strands `Agent` 1회 호출(max_tokens=8000, 도구 없음)이라 프로세스를 띄우지 않는다 |
| **§8 config 격리** | 무영향. Survey builder는 Strands 경로이고 Claude Code 바이너리와 무관하다 |
| **프론트 레이아웃** | `SurveyPanel`이 프로토타입 탭에 붙었고, `95a2876`이 그것을 `openSlug`(빌드 드로어와 공유 → 드로어 밑에 깔려 도달 불가)에서 카드별 `설문` 버튼 + 독립 `surveySlug` 상태로 고쳤다. **§7의 다운로드 버튼은 그 패턴을 그대로 따른다** — `PrototypeCard`에 옵셔널 콜백 prop을 추가하고 `SECONDARY_BTN`을 쓴다. 단 `설문`은 상태 분기 밖(스펙만 있어도 열림)이고 다운로드는 번들이 필요하므로 분기 안이다 |

## 14. 스코프 제외

- Skill·서브에이전트 실제 활성화 — `CLAUDE_CONFIG_DIR`와 `skills=`/`agents=` 자리만
  확보. 무엇을 넣을지는 별도 판단
- 업로드 원본 bytes 보존 — 변환 텍스트만 유지(기존 정책)
- 프로토타입별 추가 인증·HTTPS 서브도메인 (경로 프록시로 충분)
- 호스팅 프로세스의 systemd 상시화·자동 TTL (수동 종료 유지)
- 디스크 자동 정리(LRU 등) — 실측 23MB/건, EBS 100GB이므로 모니터링만
- 백엔드 다중 인스턴스 — `proto_sessions`·`ProtoHost` 레지스트리가 인메모리이고 빌드
  디렉토리가 로컬이므로 단일 인스턴스 전제를 유지한다. 여러 대가 필요해지면 별도
  설계 사안
- Node.js 외 런타임 (PROTOTYPE 빌드 지침이 Node/Next 스택 기본)
- 롤백 경로 — 레거시 보존 안 함(사용자 결정). git 히스토리가 유일한 복구 수단

## Open Questions

없음 — 본 설계 범위의 미결정 사항은 모두 사용자 확인으로 해소됐다.
