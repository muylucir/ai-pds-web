// frontend/lib/api/errorMessage.ts — 백엔드 에러 코드 → UI 문구.
//
// 백엔드는 UI 언어를 모르므로 안정적 코드(backend/aipds/error_codes.py)를
// detail로 보낸다. 문구는 여기가 소유한다.
//
// **모르는 코드는 원문을 그대로 돌려준다.** 코드화가 부분적으로 진행된 중간
// 상태에서도, 그리고 새 에러가 추가됐을 때도 화면이 빈 채로 남지 않게 한다 —
// 사용자가 코드를 보는 것이 아무것도 못 보는 것보다 낫다.
import type { Dict } from "@/lib/i18n";

type T = (key: keyof Dict) => string;

// 백엔드 코드 → 딕셔너리 키. 값이 `keyof Dict`이므로 오타가 컴파일 에러가 된다.
const KEY_BY_CODE: Record<string, keyof Dict> = {
  email_exists: "err.emailExists",
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
  model_not_selectable: "err.modelNotSelectable",
  language_unsupported: "err.languageUnsupported",
  build_slots_busy: "err.buildSlotsBusy",
  build_session_active: "err.buildSessionActive",
  init_incomplete: "err.initIncomplete",
  survey_closed: "err.surveyClosed",
  survey_full: "err.surveyFull",
};

export function errorMessage(t: T, detail: string): string {
  const raw = detail.trim();
  if (raw === "") return t("err.generic");
  // `init_incomplete:s3,host` — 코드 뒤의 콜론 이후는 진단 정보다. 코드만
  // 번역하고 상세는 괄호로 덧붙인다.
  const [code, ...rest] = raw.split(":");
  const key = KEY_BY_CODE[code];
  if (key === undefined) return raw;
  const detailSuffix = rest.join(":").trim();
  return detailSuffix ? `${t(key)} (${detailSuffix})` : t(key);
}
