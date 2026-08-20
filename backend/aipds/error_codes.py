# backend/aipds/error_codes.py — HTTP detail로 나가는 안정적 코드.
#
# 백엔드는 UI 언어를 모른다: 프록시(frontend/app/api/[...path]/route.ts의
# filterHeaders)가 Accept-Language를 전달하지 않고, 전달하게 만들어도 브라우저
# 값이 들어와 UI 스위치(pf_lang 쿠키)와 어긋난다. 그래서 문구를 만들지 않고
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
