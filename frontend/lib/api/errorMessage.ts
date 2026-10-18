// frontend/lib/api/errorMessage.ts — backend error code → UI text.
//
// The backend does not know the UI language, so it sends stable codes (backend/aipds/error_codes.py)
// as detail. The wording is owned here.
//
// **Unknown codes return the original text as is.** So the screen is not left blank in the intermediate
// state where codification is only partly done, or when a new error is added —
// a user seeing the code is better than seeing nothing.
import type { Dict } from "@/lib/i18n";

type T = (key: keyof Dict) => string;

// Backend code → dictionary key. The values are `keyof Dict`, so typos become compile errors.
const KEY_BY_CODE: Record<string, keyof Dict> = {
  email_exists: "err.emailExists",
  email_invalid: "err.emailInvalid",
  user_not_found: "err.userNotFound",
  bad_request: "err.badRequest",
  forbidden: "err.forbidden",
  too_many_requests: "err.tooManyRequests",
  user_admin_failed: "err.userAdminFailed",
  user_create_failed: "err.userCreateFailed",
  self_target: "err.selfTarget",
  last_admin: "err.lastAdmin",
  name_required: "err.nameRequired",
  model_id_required: "err.modelIdRequired",
  model_id_charset: "err.modelIdCharset",
  model_order_stale: "err.modelOrderStale",
  model_not_selectable: "err.modelNotSelectable",
  language_unsupported: "err.languageUnsupported",
  build_slots_busy: "err.buildSlotsBusy",
  build_session_active: "err.buildSessionActive",
  sandbox_unavailable: "err.sandboxUnavailable",
  init_incomplete: "err.initIncomplete",
  survey_closed: "err.surveyClosed",
  survey_full: "err.surveyFull",
  wrong_password: "err.wrongPassword",
  password_policy: "err.passwordPolicy",
  reauth_required: "err.reauthRequired",
  password_change_failed: "err.passwordChangeFailed",
  auth_not_configured: "err.authNotConfigured",
  export_invalid: "err.exportInvalid",
  export_too_large: "err.exportTooLarge",
  upload_missing: "err.uploadMissing",
  project_exists: "err.projectExists",
  import_unavailable: "err.importUnavailable",
  import_failed: "err.importFailed",
  project_read_only: "err.projectReadOnly",
  member_invalid: "err.memberInvalid",
  owner_not_assignable: "err.ownerNotAssignable",
  seed_admin_protected: "err.seedAdminProtected",
};

/** For a known code, the same translation as `errorMessage`; otherwise `fallback`.
 *
 *  `errorMessage` shows unknown details verbatim, but there are places where that original text is not a sentence:
 *  for the 502 on starting hosting, detail is the tail of the npm log (start_host in backend
 *  routes/prototypes). A multi-line log is not put in a one-line error slot — the log button shows the log. */
export function knownErrorMessage(t: T, detail: string, fallback: string): string {
  const code = detail.trim().split(":")[0];
  return Object.hasOwn(KEY_BY_CODE, code) ? errorMessage(t, detail) : fallback;
}

export function errorMessage(t: T, detail: string): string {
  const raw = detail.trim();
  if (raw === "") return t("err.generic");
  // `init_incomplete:s3,host` — what follows the colon after the code is diagnostic information. Only the code
  // is translated, and the details are appended in parentheses.
  const [code, ...rest] = raw.split(":");
  const key = KEY_BY_CODE[code];
  if (key === undefined) return raw;
  const detailSuffix = rest.join(":").trim();
  return detailSuffix ? `${t(key)} (${detailSuffix})` : t(key);
}
