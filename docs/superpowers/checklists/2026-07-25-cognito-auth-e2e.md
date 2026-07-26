# 인증 + 사용자 관리 수동 e2e 검증

날짜: 2026-07-25
대상: `docs/superpowers/plans/2026-07-25-cognito-auth-user-management.md`

실 AWS 배포가 필요하다(Cognito·CloudFront·EC2). 유닛 테스트로는 검증할 수 없는
것 — Hosted UI v2의 실제 렌더, 시드 계정의 로그인 가능 여부, 쿠키가 SSE까지
흐르는지 — 만 여기서 확인한다.

## 준비

```bash
cd infra
npx cdk deploy --all --require-approval never
```

출력에서 `DistributionDomain`을 적어둔다. 부팅에 5–10분 걸린다
(`aws ssm start-session` → `tail -f /var/log/pathfinder-bootstrap.log`로 확인).

## 체크리스트

- [ ] **1. 미인증 접근이 로그인으로 리다이렉트된다**
  `DistributionDomain`을 시크릿 창으로 열면 `/login?next=%2F`로 이동한다.

- [ ] **2. Hosted UI v2가 정상 렌더된다**
  "로그인" 버튼 → Cognito 로그인 화면. **깨진 레이아웃이 아니어야 한다**
  (v2는 브랜딩 레코드가 없으면 렌더가 깨진다 — `CfnManagedLoginBranding` 검증).

- [ ] **3. 회원가입 링크가 없다**
  로그인 화면에 "Sign up" 링크가 **보이지 않는다**(self-signup 차단의 육안 확인).

- [ ] **4. 시드 관리자 계정이 즉시 로그인된다**
  `admin@pathfinder.local` / `PathFinder2026!@` → **비밀번호 변경을 요구하지 않고**
  곧바로 프로젝트 목록으로 들어간다(`Permanent: true` 검증).

- [ ] **5. 헤더에 실제 사용자가 보인다**
  우상단 아바타가 `A`이고, 클릭하면 `admin@pathfinder.local` / `관리자` /
  `사용자 관리` / `로그아웃`이 보인다(하드코딩 "김PM"이 사라졌는지 확인).

- [ ] **6. 기존 기능이 인증 뒤에서 정상 동작한다**
  프로젝트 생성 → 캔버스에서 메시지 전송 → **SSE 응답이 스트리밍된다.**
  이것이 쿠키→Bearer 번역의 실증이다(실패하면 스트림이 401로 끊긴다).

- [ ] **7. 초대가 동작한다**
  `/admin/users` → "사용자 초대" → **새 이메일**(시드 계정이 아닌 것, 예:
  `pm2@pathfinder.local`) 입력, 역할 `pm` → 임시 비밀번호가 화면에 표시된다.
  복사 버튼이 동작한다. 이 계정을 아래 **"초대 계정"**으로 부른다.

- [ ] **8. 초대된 계정의 첫 로그인이 비밀번호 변경을 요구한다**
  로그아웃 후 **7의 초대 계정** + 임시 비밀번호로 로그인 → Hosted UI가 새
  비밀번호를 요구한다(`Permanent: false` 검증). 변경 후 프로젝트 목록으로
  들어간다.

- [ ] **9. pm은 사용자 관리에 접근할 수 없다**
  **8에서 로그인한 초대 계정**으로 이어서 확인한다:
  - 헤더 메뉴에 "사용자 관리" 링크가 **없다**.
  - 주소창에 `/admin/users`를 직접 입력 → 프로젝트 목록으로 되돌아간다(미들웨어).
  - (심화) 브라우저 콘솔에서 `fetch('/api/admin/users').then(r=>r.status)` →
    **403**. 미들웨어를 우회해도 백엔드가 막는다는 확인(백엔드 쪽 403 자체는
    `backend/tests/test_routes_admin_users.py`가 유닛으로 고정하지만, 여기서는
    실제 쿠키·미들웨어를 거친 뒤에도 그 방어선이 살아있는지 실물로 확인한다).

- [ ] **10. 마지막 관리자 보호가 동작한다**
  **시드 admin 계정**(`admin@pathfinder.local`)으로 로그인 → `/admin/users` →
  자기 행의 역할을 `PM`으로 바꾸려 하면 "자신의 계정은 강등할 수 없습니다"
  메시지가 뜬다. 삭제·비활성화도 같다.

- [ ] **11. 비밀번호 재설정이 동작한다**
  **시드 pm 계정**(`pm@pathfinder.local`)의 "비밀번호 재설정" → 새 임시
  비밀번호 표시 → 그 값으로 로그인되고 변경을 요구한다.

- [ ] **12. 비활성화된 계정은 로그인할 수 없다**
  **시드 pm 계정**(`pm@pathfinder.local`, 11에서 재설정한 그 계정)을
  "비활성화" → 그 계정으로 로그인 시도 → Hosted UI가 거부한다.

- [ ] **13. 익명 설문이 로그아웃 상태에서 동작한다**
  프로토타입 탭에서 설문을 만들어 링크를 복사 → 시크릿 창에서 열어 **로그인 없이**
  응답을 제출한다(공개 경로 목록 자체는
  `backend/tests/test_auth_route_coverage.py`가 유닛으로 고정하지만, 여기서는
  실제 CloudFront·쿠키 상태에서 익명 접근이 실제로 되는지 확인한다).

- [ ] **14. 프로토타입 프리뷰가 로그아웃 상태에서 열린다**
  호스팅 중인 프로토타입 URL을 시크릿 창에서 열면 앱이 뜬다(마찬가지로
  `test_auth_route_coverage.py`가 경로 목록을 고정하지만, 실제 EC2 프록시를
  거친 렌더는 여기서만 확인된다).

- [ ] **15. 로그아웃이 Cognito 세션까지 끊는다**
  로그아웃 → 다시 "로그인" → **비밀번호를 다시 묻는다**(곧바로 통과하면
  Cognito 세션이 남은 것이다).

- [ ] **16. 재배포가 시드 비밀번호를 되돌린다**
  admin 계정의 비밀번호를 임의로 바꾼 뒤 `npx cdk deploy --all` → 다시
  `PathFinder2026!@`로 로그인된다(`onUpdate` 검증). 재배포가 사용자를 지우거나
  스택을 롤백하지 않는다(`ignoreErrorCodesMatching` 검증).

## 정리

```bash
cd infra && npx cdk destroy --all
```

- [ ] **17. User Pool과 사용자 전원이 함께 삭제됐다**
  Cognito 콘솔에서 `pathfinder` User Pool이 더 이상 보이지 않는지 확인한다
  (`RemovalPolicy.DESTROY` 검증 — 초대해서 만든 계정을 포함해 전원이 사라진다).
