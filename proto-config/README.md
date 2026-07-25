# proto-config — 프로토타입 빌드 에이전트의 CLAUDE_CONFIG_DIR

이 디렉토리는 프로토타입 빌드 에이전트(Claude Agent SDK)의 config 루트다.
`PATHFINDER_PROTO_CONFIG_DIR`가 여기를 가리키고, 그 값은 SDK에
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

CDK 에셋 zip이 레포 루트를 `/opt/pathfinder/`로 전개하므로, 이 디렉토리는
별도 복사 단계 없이 `/opt/pathfinder/proto-config/`가 된다. 로컬 개발에서는
`PATHFINDER_PROTO_CONFIG_DIR`로 이 디렉토리를 직접 가리키면 된다:

```bash
# backend/.env
PATHFINDER_PROTO_CONFIG_DIR=/abs/path/to/repo/proto-config
```

## 스킬 추가 방법

1. `skills/<name>/SKILL.md`를 만든다(frontmatter의 `name`은 디렉토리명과 일치).
2. 커밋한다. 끝 — `PrototypeBuilder`가 `skills="all"`로 열려 있어
   **디스커버된 스킬이 자동으로 활성화**된다. 코드 변경이 필요 없다.

`skills="all"`의 대가: 여기 파일을 넣는 순간 켜진다. 실험용 스킬을 임시로 두면
그대로 워크숍 빌드에 들어가므로, 켜고 싶지 않은 것은 커밋하지 않는다.

## 런타임에 생기는 것 (gitignored)

- `projects/` — SDK가 transcript 로컬 사본을 쌓는다. 영속 사본은 S3의
  `projects/{pid}/prototypes/{slug}/transcript/`이고 이건 재개용 캐시다.
- `.credentials.json` / `.claude.json` — Bedrock 경로에서는 쓰이지 않지만 SDK가
  이 위치를 참조한다.
