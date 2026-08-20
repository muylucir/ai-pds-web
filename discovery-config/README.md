# discovery-config

Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR` (`AIPDS_DISCOVERY_CONFIG_DIR`).

## 왜 proto-config와 분리하는가

`proto-config/CLAUDE.md`는 "프로토타입 디자인은 shadcn-design 스킬을 사용"을
지시한다. 이 지시가 Discovery에 들어가면 문서 작성 중 무관한 UI 스킬을 로드한다.
빌더는 그 스킬을 이름으로 켜므로(`builder.py`의 `skills=["shadcn-design"]`)
config dir을 공유하면 Discovery도 같은 스킬을 켠 채로 돌게 된다. 역방향도 같다:
여기의 `submit_document` 규약과 질문 파일·상태 파일 규약이 빌더에 들어가면
존재하지 않는 도구를 부르거나 빌더에 없는 파일을 찾으려 한다.

Discovery는 `skills`를 아예 주지 않는다(아래 "skills를 두지 않는다" 참조).
빌더가 이름 목록을 쓰는 이유는 `proto-config/README.md`에 있다 — 예전의
`skills="all"`이 CLI 번들 스킬까지 켜서 2026-08-01에 프론트엔드를 죽였다.

미지정 시 호스트 유저의 `~/.claude`(개인 skills/agents/CLAUDE.md)가 섞여 워크숍
결과가 호스트 설정에 따라 달라진다 — 그래서 격리된 값을 반드시 준다.

## AI-PLC 룰은 여기 두지 않는다

룰은 **워크스페이스**로 간다(`agent/workspace_rules.py`가 배치).
`core-workflow.md:18`이 `Rule details location: ./aws-aiplc-rule-details/`로
CWD 상대경로를 전제하므로, config dir에 두면 그 경로가 맞지 않는다.

| 디렉터리 | 내용 |
|---|---|
| `rule/aiplc-rules/` | 상류 룰 원본(읽기 전용 마스터) |
| 워크스페이스 `{project_id}/` | `CLAUDE.md` + `aws-aiplc-rule-details/` 사본 + 산출물 |
| `discovery-config/` | 이 파일과 통합 규약 `CLAUDE.md`만 |

## 상류 룰과 어긋나는 부분은 CLAUDE.md에서 덮는다

`rule/aiplc-rules/`는 읽기 전용 마스터다 — 상류가 갱신되면 사본이 덮이므로 룰
본문을 직접 고치면 그 수정은 유실된다. AI-PDS의 구조와 맞지 않는 지시는
`CLAUDE.md`에 **무엇을 하지 말라고 명시해** 덮는다.

현재 덮고 있는 것: `prototype-building.md`의 빌드·실행 단계와 포트 지정
(`Port: {3000 + X}`). 상류 룰은 사람이 로컬에서 직접 돌리는 워크숍을 전제하지만,
AI-PDS에서는 프로토타입 탭의 `ProtoHost`만이 포트를 배정하고 프리뷰 프록시에
등록할 수 있다 — Discovery가 스스로 띄운 서버는 어느 화면에도 나타나지 않는다.
그래서 분업은 **Discovery는 스펙까지, 빌드는 프로토타입 탭**이다. 경계는
`routes/prototypes.py`의 `_SPEC_RE`가 정하는 스펙 경로 규약이다.

## skills를 두지 않는다

상류 AI-PLC 셋업은 skills를 쓰지 않는다(CLAUDE.md + 온디맨드 파일 읽기). 룰을
SKILL.md로 승격하면 상류 업데이트를 받아올 수 없고 룰 본문의 읽기 지시와
충돌한다.
