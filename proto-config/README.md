# proto-config — 프로토타입 빌드 에이전트의 CLAUDE_CONFIG_DIR

이 디렉토리는 프로토타입 빌드 에이전트(Claude Agent SDK)의 config 루트다.
`AIPDS_PROTO_CONFIG_DIR`가 여기를 가리키고, 그 값은 SDK에
`CLAUDE_CONFIG_DIR`로 전달된다.

**이 디렉토리가 곧 `~/.claude`와 동급이다.** `CLAUDE_CONFIG_DIR`가 설정되면 SDK는
그 경로를 config 루트로 그대로 쓴다(`sessions.py`의 `_get_projects_dir`:
`override / "projects"`). 미설정일 때만 `~/.claude`를 붙인다. 따라서:

- 스킬은 `proto-config/skills/<name>/SKILL.md`
- 서브에이전트는 `proto-config/agents/<name>.md`
- **`proto-config/.claude/...`는 만들지 않는다** — 만들면 SDK가 보지 않는다

## 왜 이 디렉토리가 존재하는가

지정하지 않으면 번들 Claude Code 바이너리가 백엔드 실행 유저의 `~/.claude`를
읽는다. 그러면 워크숍/고객 배포마다 **호스트에 우연히 있던 개인 스킬·에이전트·
CLAUDE.md가 빌드 결과에 섞여** 재현이 되지 않는다. 여기로 갈아끼워 그 경로를
차단하고, 대신 **우리가 의도한 것만** 넣는다.

## 배포 경로

CDK 에셋 zip이 레포 루트를 `/opt/aipds/`로 전개하므로, 이 디렉토리는
별도 복사 단계 없이 `/opt/aipds/proto-config/`가 된다. 로컬 개발에서는
`AIPDS_PROTO_CONFIG_DIR`로 이 디렉토리를 직접 가리키면 된다:

```bash
# backend/.env
AIPDS_PROTO_CONFIG_DIR=/abs/path/to/repo/proto-config
```

## 스킬 추가 방법

1. `skills/<name>/SKILL.md`를 만든다(frontmatter의 `name`은 디렉토리명과 일치).
2. **`backend/pathfinder/proto/builder.py`의 `skills=[...]` 목록에 이름을 넣는다.**
   목록에 없으면 파일이 있어도 켜지지 않는다.

현재 켜져 있는 것은 `shadcn-design` 하나다.

**왜 이름 목록인가(2026-08-01의 사고).** 예전에는 `skills="all"`이었고, README도
"커밋하면 끝"이라고 안내했다. 그 전제는 "config dir 아래 것만 켜진다"였는데
**틀렸다** — `"all"`은 CLI에 번들된 스킬까지 함께 켜고, 그 목록에 `run`("Launch
and drive this project's app… browser-driven")이 있다. 빌드 에이전트가 그
스킬로 Playwright chromium을 띄웠고, 검증이 포트 3000을 겨냥해 Pathfinder
프론트엔드가 SIGKILL로 죽었다(journalctl status=9/KILL). 백엔드·프론트엔드가
빌드 에이전트와 같은 유저로 돌므로 막을 것이 없었다.

그래서 **"커밋 한 번으로 추가"의 편의를 의도적으로 포기했다.** 스킬을 추가할 때
코드도 한 줄 고치는 것이 그 대가다.

이 목록은 **컨텍스트 필터이지 샌드박스가 아니다** — 스킬을 숨길 뿐 Bash 자체를
막지는 못한다. 브라우저·서버 기동·프로세스 종료는 PreToolUse 훅이 코드로 거부한다
(`proto/build_guard.py`).

## 런타임에 생기는 것 (gitignored)

- `projects/` — SDK가 transcript 로컬 사본을 쌓는다. 영속 사본은 S3의
  `projects/{pid}/prototypes/{slug}/transcript/`이고 이건 재개용 캐시다.
- `.credentials.json` / `.claude.json` — Bedrock 경로에서는 쓰이지 않지만 SDK가
  이 위치를 참조한다.
