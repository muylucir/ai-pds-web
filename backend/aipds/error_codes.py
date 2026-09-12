# backend/aipds/error_codes.py — HTTP detail로 나가는 안정적 코드.
#
# 백엔드는 UI 언어를 모른다: 프록시(frontend/app/api/[...path]/route.ts의
# filterHeaders)가 Accept-Language를 전달하지 않고, 전달하게 만들어도 브라우저
# 값이 들어와 UI 스위치(aipds_lang 쿠키)와 어긋난다. 그래서 문구를 만들지 않고
# 코드를 보내며, 문구는 프론트 딕셔너리가 소유한다
# (frontend/lib/api/errorMessage.ts).
#
# 여기에 두 번째 번역 시스템을 만들지 않는 이유가 그것이다 — UI 언어의 단일
# 출처는 이미 프론트에 있다. 예외는 survey/report_labels.py인데, 그쪽은 UI
# 문구가 아니라 문서 생성기이고 프로젝트 언어를 이미 백엔드가 안다.
#
# 값은 snake_case이고 **바꾸지 않는다** — 프론트 딕셔너리의 키가 이 값에
# 달려 있다. 새 에러는 여기에 상수를 추가하고 양쪽 딕셔너리에 키를 넣는다.
from __future__ import annotations

# 사용자 관리 (routes/admin_users.py)
EMAIL_EXISTS = "email_exists"
USER_NOT_FOUND = "user_not_found"
BAD_REQUEST = "bad_request"
FORBIDDEN = "forbidden"
TOO_MANY_REQUESTS = "too_many_requests"
USER_ADMIN_FAILED = "user_admin_failed"
USER_CREATE_FAILED = "user_create_failed"
# 자기 계정 / 마지막 관리자 보호. 어떤 조작이었는지(강등·비활성화·삭제)는
# 코드에 싣지 않는다 — 프론트가 그 어휘를 UI 언어로 갖고 있어야 하는데,
# 조작 종류는 이미 사용자가 누른 버튼으로 화면에 드러나 있다.
SELF_TARGET = "self_target"
LAST_ADMIN = "last_admin"

# 자기 비밀번호 변경 (routes/account.py)
#
# WRONG_PASSWORD와 PASSWORD_POLICY를 나누는 이유는 사용자가 할 일이 다르기
# 때문이다 — 앞은 현재 비밀번호를 다시 입력하는 것, 뒤는 새 비밀번호를 다시
# 고르는 것이다. 하나로 뭉치면 화면이 어느 칸이 틀렸는지 말할 수 없다.
#
# The `nosec` markers are for the names: these are error codes the frontend
# switches its message on, and bandit B105 judges an assignment by the target's
# name alone.
WRONG_PASSWORD = "wrong_password"  # nosec B105
PASSWORD_POLICY = "password_policy"  # nosec B105
# 이 세션의 토큰으로는 비밀번호를 바꿀 수 없다(셀프서비스 스코프 없음, 관리자가
# 재설정해 계정이 임시 비밀번호 상태로 돌아감 등). 사용자가 할 일은 입력을 고치는
# 것이 아니라 다시 로그인하는 것이다.
REAUTH_REQUIRED = "reauth_required"
PASSWORD_CHANGE_FAILED = "password_change_failed"  # nosec B105
# Cognito가 아예 설정되지 않은 환경(로컬 개발)에서 이 기능을 부를 수는 없다.
# 조용히 성공하면 "비밀번호를 바꿨다"는 거짓 확인을 주게 된다.
AUTH_NOT_CONFIGURED = "auth_not_configured"

# 모델 카탈로그 (routes/models.py)
NAME_REQUIRED = "name_required"
MODEL_ID_REQUIRED = "model_id_required"
MODEL_ID_CHARSET = "model_id_charset"

# 프로젝트 (routes/projects.py)
MODEL_NOT_SELECTABLE = "model_not_selectable"
LANGUAGE_UNSUPPORTED = "language_unsupported"

# 프로토타입 (routes/prototypes.py)
BUILD_SLOTS_BUSY = "build_slots_busy"
BUILD_SESSION_ACTIVE = "build_session_active"
# 초기화 실패는 무엇이 실패했는지가 진단에 필요하다. 코드 뒤에 콜론으로 붙여
# 보내고(`init_incomplete:s3,host`) 프론트는 코드 부분만 번역한다.
INIT_INCOMPLETE = "init_incomplete"

# 공개 설문 (routes/surveys_public.py)
SURVEY_CLOSED = "survey_closed"
SURVEY_FULL = "survey_full"

# 프로젝트 이관 (routes/transfer.py)
#
# EXPORT_INVALID와 UPLOAD_MISSING을 나누는 이유는 사용자가 할 일이 다르기
# 때문이다 — 앞은 다른 파일을 고르는 것, 뒤는 업로드를 다시 하는 것이다
# (스테이징 서명이 만료됐거나 PUT이 조용히 실패한 상태).
EXPORT_INVALID = "export_invalid"
EXPORT_TOO_LARGE = "export_too_large"
UPLOAD_MISSING = "upload_missing"
# 대상 id가 이미 있다. 화면은 이 코드를 받아 id 입력 칸을 열고 **같은 업로드로**
# 다시 부른다 — 그래서 이 응답은 스테이징을 지우지 않는다.
PROJECT_EXISTS = "project_exists"
# 버킷이 설정되지 않아 스테이징이 없다(로컬 개발). 내보내기는 되고 가져오기만 막힌다.
IMPORT_UNAVAILABLE = "import_unavailable"
IMPORT_FAILED = "import_failed"
