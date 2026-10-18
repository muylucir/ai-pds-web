# backend/aipds/error_codes.py — stable codes sent out as the HTTP detail.
#
# The backend does not know the UI language: the proxy (filterHeaders in
# frontend/app/api/[...path]/route.ts) does not forward Accept-Language, and even
# if it did, the browser's value would come in and disagree with the UI switch
# (the aipds_lang cookie). So the backend sends codes instead of composing
# messages, and the frontend dictionary owns the messages
# (frontend/lib/api/errorMessage.ts).
#
# That is why no second translation system is built here -- the single source
# of the UI language already lives in the frontend. The exception is
# survey/report_labels.py, which is a document generator rather than UI text,
# and the backend already knows the project language.
#
# Values are snake_case and **never change** -- the frontend dictionary keys
# depend on them. For a new error, add a constant here and add the key to both
# dictionaries.
from __future__ import annotations

# User management (routes/admin_users.py)
EMAIL_EXISTS = "email_exists"
# The invitee's email is malformed. The browser's type=email lets `kim@corp` or a
# `.local` domain through, so the server-side rejection path is real.
EMAIL_INVALID = "email_invalid"
USER_NOT_FOUND = "user_not_found"
BAD_REQUEST = "bad_request"
FORBIDDEN = "forbidden"
TOO_MANY_REQUESTS = "too_many_requests"
USER_ADMIN_FAILED = "user_admin_failed"
USER_CREATE_FAILED = "user_create_failed"
# Own-account / last-admin protection. Which operation it was (demote, disable,
# delete) is not carried in the code -- the frontend would need that vocabulary
# in the UI language, and the kind of operation is already evident on screen
# from the button the user pressed.
SELF_TARGET = "self_target"
LAST_ADMIN = "last_admin"

# Changing one's own password (routes/account.py)
#
# WRONG_PASSWORD and PASSWORD_POLICY are separate because the user's next step
# differs -- the first means re-enter the current password, the second means
# pick a new password again. Lumped together, the screen cannot say which field
# was wrong.
#
# The `nosec` markers are for the names: these are error codes the frontend
# switches its message on, and bandit B105 judges an assignment by the target's
# name alone.
WRONG_PASSWORD = "wrong_password"  # nosec B105
PASSWORD_POLICY = "password_policy"  # nosec B105
# This session's token cannot change the password (no self-service scope, an
# admin reset put the account back into the temporary-password state, etc.).
# The user's next step is not to fix the input but to sign in again.
REAUTH_REQUIRED = "reauth_required"
PASSWORD_CHANGE_FAILED = "password_change_failed"  # nosec B105
# This feature cannot be called where Cognito is not configured at all (local
# development). Silently succeeding would give a false "password changed"
# confirmation.
AUTH_NOT_CONFIGURED = "auth_not_configured"

# Model catalog (routes/models.py)
NAME_REQUIRED = "name_required"
MODEL_ID_REQUIRED = "model_id_required"
MODEL_ID_CHARSET = "model_id_charset"
# When saving the order, the list on the admin screen differs from the stored
# list (it was changed in another tab).
MODEL_ORDER_STALE = "model_order_stale"

# Projects (routes/projects.py)
MODEL_NOT_SELECTABLE = "model_not_selectable"
LANGUAGE_UNSUPPORTED = "language_unsupported"

# Prototypes (routes/prototypes.py)
BUILD_SLOTS_BUSY = "build_slots_busy"
BUILD_SESSION_ACTIVE = "build_session_active"
# The sandbox wrapper is on but unusable (aipds/launcher.py) -- an operator must fix it.
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
# For a failed reset, diagnosis needs to know what failed. It is sent appended
# after a colon (`init_incomplete:s3,host`) and the frontend translates only the
# code part.
INIT_INCOMPLETE = "init_incomplete"

# Public survey (routes/surveys_public.py)
SURVEY_CLOSED = "survey_closed"
SURVEY_FULL = "survey_full"

# Project transfer (routes/transfer.py)
#
# EXPORT_INVALID and UPLOAD_MISSING are separate because the user's next step
# differs -- the first means pick a different file, the second means upload
# again (the staging signature expired or the PUT silently failed).
EXPORT_INVALID = "export_invalid"
EXPORT_TOO_LARGE = "export_too_large"
UPLOAD_MISSING = "upload_missing"
# The target id already exists. The screen takes this code, opens the id input,
# and calls again **with the same upload** -- so this response does not delete
# the staging.
PROJECT_EXISTS = "project_exists"
# No bucket configured, so there is no staging (local development). Export works;
# only import is blocked.
IMPORT_UNAVAILABLE = "import_unavailable"
IMPORT_FAILED = "import_failed"

# Project access (project_access.py, routes/projects.py, routes/admin_users.py)
#
# A read-only member attempted a write. The screen does not draw write buttons
# for members, so the normal path never gets here -- it happens when permissions
# changed while a tab was open.
PROJECT_READ_ONLY = "project_read_only"
# A value that cannot be used as a member or owner (neither an email nor a username).
MEMBER_INVALID = "member_invalid"
# An account that cannot receive ownership (disabled, no role). A nonexistent
# account is USER_NOT_FOUND.
OWNER_NOT_ASSIGNABLE = "owner_not_assignable"
# The seed admin is the attribution target, so it cannot be deleted or demoted
# (project_access.SEED_ADMIN).
SEED_ADMIN_PROTECTED = "seed_admin_protected"
