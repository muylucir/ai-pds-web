# backend/aipds/error_codes.py -- the stable codes that go out as an HTTP detail.
#
# The backend does not know the UI language: the proxy (filterHeaders in
# frontend/app/api/[...path]/route.ts) does not forward Accept-Language, and making it
# forward would bring in the browser's value, which disagrees with the UI switch (the
# aipds_lang cookie). So it sends a code rather than building wording, and the wording is
# owned by the frontend dictionary (frontend/lib/api/errorMessage.ts).
#
# That is why no second translation system is built here -- the single source for the UI
# language is already in the frontend. The exception is survey/report_labels.py, which is a
# document generator rather than UI wording, and there the backend already knows the
# project language.
#
# The values are snake_case and **must not change** -- the frontend dictionary's keys
# depend on them. A new error means adding a constant here and a key to both
# dictionaries.
from __future__ import annotations

# User administration (routes/admin_users.py)
EMAIL_EXISTS = "email_exists"
USER_NOT_FOUND = "user_not_found"
BAD_REQUEST = "bad_request"
FORBIDDEN = "forbidden"
TOO_MANY_REQUESTS = "too_many_requests"
USER_ADMIN_FAILED = "user_admin_failed"
USER_CREATE_FAILED = "user_create_failed"
# Protecting one's own account and the last administrator. Which operation it was
# (demotion, deactivation, deletion) is not carried in the code -- the frontend would have
# to hold that vocabulary in the UI language, and the kind of operation is already evident
# on screen from the button the user pressed.
SELF_TARGET = "self_target"
LAST_ADMIN = "last_admin"

# The model catalogue (routes/models.py)
NAME_REQUIRED = "name_required"
MODEL_ID_REQUIRED = "model_id_required"
MODEL_ID_CHARSET = "model_id_charset"

# Projects (routes/projects.py)
MODEL_NOT_SELECTABLE = "model_not_selectable"
LANGUAGE_UNSUPPORTED = "language_unsupported"

# Prototypes (routes/prototypes.py)
BUILD_SLOTS_BUSY = "build_slots_busy"
BUILD_SESSION_ACTIVE = "build_session_active"
# For an initialisation failure, diagnosis needs to know what failed. It is appended after
# the code with a colon (`init_incomplete:s3,host`) and the frontend translates only the
# code part.
INIT_INCOMPLETE = "init_incomplete"

# Public surveys (routes/surveys_public.py)
SURVEY_CLOSED = "survey_closed"
SURVEY_FULL = "survey_full"
