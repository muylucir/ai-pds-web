# discovery-config

Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR` (`PATHFINDER_DISCOVERY_CONFIG_DIR`).

## 왜 proto-config와 분리하는가

`proto-config/CLAUDE.md`는 "프로토타입 디자인은 shadcn-design 스킬을 사용"을
지시한다. 이 지시가 Discovery에 들어가면 문서 작성 중 무관한 UI 스킬을 로드한다.
게다가 프로토타입 빌더는 `skills="all"`이므로 **config dir의 모든 스킬이
활성화**된다 — 공유하면 Discovery가 shadcn-design을 켠 채로 돈다. 역방향도 같다:
여기의 `report_stage`/`submit_document` 규약이 빌더에 들어가면 존재하지 않는
도구를 부르려 한다.

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

## skills를 두지 않는다

상류 AI-PLC 셋업은 skills를 쓰지 않는다(CLAUDE.md + 온디맨드 파일 읽기). 룰을
SKILL.md로 승격하면 상류 업데이트를 받아올 수 없고 룰 본문의 읽기 지시와
충돌한다.
