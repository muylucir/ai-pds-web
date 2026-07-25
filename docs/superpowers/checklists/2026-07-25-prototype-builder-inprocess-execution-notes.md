# 프로토타입 빌더 흡수 — 실행 기록 (defects found & rulings)

날짜: 2026-07-25
브랜치: `refactor/prototype-builder-inprocess` (16 커밋, base `b1db342`)
계획: `docs/superpowers/plans/2026-07-25-prototype-builder-inprocess.md`
설계: `docs/superpowers/specs/2026-07-25-prototype-builder-inprocess-design.md`

이 문서는 12개 태스크를 실행하면서 **발견해 고친 결함**과 **의도적으로 남긴
판단**을 남긴다. 작업용 리포트(태스크별 보고서·리뷰 verdict)는
`.superpowers/sdd/`에 있었으나 그 트리는 gitignore(`*`)이므로 영속되지 않는다 —
남길 가치가 있는 내용만 여기로 옮겼다.

최종 검증: backend **405** passed · frontend **357** passed + `tsc --noEmit` clean ·
infra 어서션 6종 + `cdk synth` OK.

---

## 고친 결함 4건

모두 고치기 전에 재현하고, 고친 뒤 다시 검증했다.

### 1. resume 시 transcript 첫 배치가 덮어써짐 (Critical) — `5d12333`

`S3SessionStore._seq`가 인스턴스마다 0에서 시작했다. resume은 **새 인스턴스**를
만들므로 첫 append가 `00000001.jsonl`을 재사용해 원 세션의 첫 배치를 지웠다.

재현: 두 인스턴스로 같은 session_id에 쓰면 `[u1,a1]` → `[u2,a1]` — 첫 사용자
메시지 유실 + 순서 붕괴. **이 브랜치의 존재 이유인 기능을 조용히 파괴**하는
결함이었다.

수정: 카운터를 S3의 기존 키에서 seed하고, **세션 프리픽스별로** 추적한다(한
인스턴스가 main transcript와 subagent subpath를 동시에 쓰기 때문).

### 2. 호스팅 포트 예약 누수 (Important) — `3231d05`

npm start `create_subprocess_exec`가 **raise**하면 `entry.port` 할당 전에
빠져나가므로, `stop()`의 `if entry.port is not None` 해제가 영원히 도달 못 한다.
반복 실패 시 `port_range` 전체가 소진된다.

수정: spawn을 try/except로 감싸 예약을 해제하고 failed로 표시한 뒤 re-raise.
나머지 실패 경로도 확인 — install/build 실패는 포트 확보 전에 return하고,
`_wait_for_port` 타임아웃은 `entry.port` 할당 뒤라 `stop()`이 회수한다.

### 3. 턴 중 실패가 빌드 슬롯을 영구 소모 (Important) — `4277c98`

`send_message`가 raise하면 `status="failed"` 후 re-raise만 하고 `close()`는
호출되지 않아 `semaphore.release()`가 실행되지 않는다. 라우트는 재시도 시 죽은
세션을 registry에서 **evict**하므로 그 슬롯은 아무도 해제할 수 없다.

기본 상한이 2이므로 **두 번의 턴 실패로 백엔드 전체 빌드가 잠기고**, 사용자에게는
"다른 팀이 빌드 중"이라는 잘못된 429로 보인다.

수정: 실패 경로에서 해제하되 `_slot_released` 플래그로 이중 해제를 차단한다
(`BuildSemaphore.release()`는 0에서 clamp하므로 과잉 해제를 스스로 감지하지 못하고,
**다른 세션의 슬롯을 잘못 반납**하게 된다 — 원 버그보다 나쁘다).

검증한 인터리빙 5종: raise 단독 / raise+close / raise+close+close / 정상+close /
raise+유휴만료 — 모두 정확히 한 번 해제, 2번째 보유자의 슬롯 불변.

### 4. 빌드 완료 후 카드가 "스펙만 있음"으로 표시 (Important) — `eb37f23`

`built` 상태를 `prototypes/{slug}/bundle/` S3 프리픽스에서 판정했는데, in-place
호스팅 전환 후 **그 프리픽스에 쓰는 코드가 없다**(리더 2개, 라이터 0개 — grep 확인).

결과: 빌드 성공 후에도 상태가 `none`이고, `PrototypeCard`는 **호스팅 시작 ·
다시 빌드 · 다운로드를 모두 `built` 분기 안에서만** 렌더하므로 완성된 프로토타입에
"빌드 시작" 버튼만 남았다. `GET .../archive`는 200을 반환하지만 **UI에서 도달할
수단이 없었다** — 개발팀 인계 zip이 사실상 사용 불가.

수정: 로컬 빌드 디렉토리의 `prototype/` 서브트리로 판정(S3는 fallback 유지).
`prototype/`을 보는 이유: `PrototypeSession.start()`가 에이전트 작업 전에 스펙
파일을 빌드 디렉토리에 심으므로, 디렉토리 존재만 보면 **거울상 버그**(세션만
시작해도 built)로 바뀐다. 3방향 검증: 스펙만 → none, 세션만 시작 → none,
실제 산출물 → built.

---

## 남긴 판단 (parked, 조치 없음)

- **`test_same_name_uploads_do_not_overwrite`는 구 코드에서도 통과한다.**
  순차 업로드는 구 스킴에서도 `a.md`/`a-2.md`로 갈려 둘 다 읽히므로, 이 테스트는
  의도만 문서화하고 레이스를 고정하지 못한다. 키 형태 변경 자체는
  `test_upload_md_saved_under_a_uuid_directory`가 고정하고(구 코드에서 실패),
  실제 레이스는 별도 `asyncio.gather` 재현으로 확인했다. 커버리지 공백이 아니라
  테스트 정밀도 문제로 판단해 남긴다.
- **슬롯 해제 회귀 테스트 3개 중 1개(`..._raise_then_close_releases_only_once`)도
  구 코드에서 통과한다** — 구 코드에서도 net 1회 해제라서. 해당 성질은
  `..._idle_timer_after_mid_turn_raise_...`가 고정한다(구 코드에서 실패 + 2번째
  보유자 슬롯을 단언).
- **pid 재사용 TOCTOU** (`ProtoHost.sweep_orphans`): 설계상 best-effort로 수용.
- **백엔드 단일 인스턴스 전제**: 레지스트리가 인메모리이고 빌드 디렉토리가
  로컬이다. 여러 대로 늘리려면 별도 설계 사안(설계서 §14 스코프 제외).

## 향후 리팩터 시 주의

- **인계 zip의 survey/transcript 제외는 `_archive_entries`의 `bundle_prefix`
  스코핑이 담보한다** — `_archive_excluded`의 블록리스트가 아니다. 그 함수를
  리팩터하면서 프리픽스를 넓히면 익명 응답자 원문이 조용히 zip에 들어간다.
  (프리픽스를 `prototypes/{slug}/`로 바꿔 테스트가 깨지는 것을 확인했다.)
- **`CLAUDE_CONFIG_DIR`는 `ClaudeAgentOptions` 생성 지점 단 한 곳에서 무조건
  주입된다**(`builder.py`). 분기를 추가할 때 이 불변식을 깨면 백엔드 실행 유저의
  개인 `~/.claude`(skills/agents/CLAUDE.md)가 워크숍 빌드에 섞인다.

## 사람이 해야 할 남은 작업

배포된 도쿄 스택은 코드에서 제거됐을 뿐 AWS에는 남아 있다:

```bash
cd infra && npx cdk destroy PathfinderVmStack --region ap-northeast-1
```

미실행 시 MicroVM 이미지 스토리지 비용이 계속 발생한다.
