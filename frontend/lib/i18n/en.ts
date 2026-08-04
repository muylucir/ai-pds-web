// frontend/lib/i18n/en.ts — English UI strings.
//
// `Record<keyof typeof ko, string>` 타입이 ko.ts와 키 집합이 정확히 같음을
// 강제한다. 키를 빠뜨리면 컴파일 에러이므로, 런타임에 `undefined`가 화면에
// 뜨는 일이 없다. `as const`를 쓰지 않는 이유: 그러면 값의 리터럴 타입까지
// 좁혀져서 ko와 값이 다르다는 이유로 타입이 어긋난다.
import type { ko } from "./ko";

export const en: Record<keyof typeof ko, string> = {
  "nav.dashboard": "Dashboard",
  "nav.workspace": "Workspace",
  "nav.review": "Document Review",
  "nav.prototypes": "Prototypes",
  "nav.ariaLabel": "Main menu",
  "nav.needProject": "Select a project first",
  "header.modelBadgeTitle": "The AI model this project runs on",
  "header.bedrockConnected": "Bedrock connected",
  "header.languageBadgeTitle": "Language of this project's documents, prototypes, and chat",
  "chat.answersSubmitted": "Answers submitted",
  "stream.turnError": "Something went wrong while processing this turn.",
  "stream.buildError": "Something went wrong during the build.",
  "stream.disconnected": "The connection dropped. Please try again.",
  "err.generic": "The request failed.",
  "err.emailExists": "That email is already registered.",
  "err.userNotFound": "User not found.",
  "err.badRequest": "The request was not valid.",
  "err.forbidden": "You do not have permission to do that.",
  "err.tooManyRequests": "Too many requests. Please try again shortly.",
  "err.userAdminFailed": "The user management request failed.",
  "err.userCreateFailed": "Could not create the user. Please try again.",
  "err.selfTarget": "You cannot do this to your own account. Ask another administrator.",
  "err.lastAdmin": "You cannot do this to the last administrator. Assign another one first.",
  "err.nameRequired": "Enter a name.",
  "err.modelIdRequired": "Enter a model ID.",
  "err.modelIdCharset": "A model ID may contain only letters, digits, '.', '-', '_', and ':'.",
  "err.modelNotSelectable": "That model cannot be selected.",
  "err.languageUnsupported": "That language is not supported.",
  "err.buildSlotsBusy": "Another team is building a prototype — please try again shortly.",
  "err.buildSessionActive": "A build session is running — close it first.",
  "err.initIncomplete": "Initialization did not finish — please try again.",
  "err.surveyClosed": "This survey is closed.",
  "err.surveyFull": "The response limit has been reached. Please close the survey.",
};
