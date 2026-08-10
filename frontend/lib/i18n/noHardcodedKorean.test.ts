// frontend/lib/i18n/noHardcodedKorean.test.ts
//
// **UI 소스에 한국어 리터럴이 없어야 한다.** 딕셔너리(ko.ts)와 주석만 예외다.
//
// 왜 이 테스트가 생겼는가(2026-08-04). 사용자가 영어 UI에서 문서 뷰어의
// "아직 저장되지 않은 문서입니다…"를 신고했다. 전수 점검 결과 그 한 줄이 아니라
// **16곳**이 같은 상태였다 — 질문 폼의 제출 버튼, 채팅 하단 감사 안내, 문서
// 승인/수정요청 버튼, 리뷰 화면 감사 문구 등.
//
// 기존 테스트가 전부 통과하는 동안 그것들이 살아 있었던 이유가 이 테스트의
// 존재 이유다: 컴포넌트 테스트는 기본 로케일(ko)로 렌더하고 한국어 문자열을
// 단정하므로, **번역된 문구와 하드코딩된 문구가 구별되지 않는다.** 둘 다
// 통과한다. 그리고 스펙 5단계의 "65개 파일 전수 치환"은 파일 단위 작업이었는데,
// 누락분은 전부 `t()`를 이미 쓰는 파일 **안에** 남아 있어서 파일 단위로는
// 완료된 것처럼 보였다.
//
// 그래서 판정을 사람의 훑어보기에서 기계로 옮긴다. 새 화면을 만들면서 한국어를
// 직접 박으면 이 테스트가 즉시 실패한다.
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = join(__dirname, "..", "..");
const SCAN_DIRS = ["app", "components", "lib"];

/** 한글 음절. 자모까지 넣지 않는 이유는 실제 산문에는 완성형만 나오기 때문이다. */
const HANGUL = /[가-힣]/;

/** 딕셔너리 자신과 테스트는 한국어를 담는 것이 일이다. */
function isExempt(rel: string): boolean {
  if (rel.includes("lib/i18n/ko.ts")) return true;
  if (/\.test\.(ts|tsx)$/.test(rel)) return true;
  return false;
}

/** 한국어가 남아 있어야 **하는** 줄. 각 항목에 이유가 있다 — UI 언어와 무관한
 *  값들이고, 딕셔너리로 옮기면 오히려 깨진다.
 *
 *  전부 `파일:일부문자열` 쌍이다. 줄 번호로 고정하지 않는 이유: 위에 한 줄
 *  추가하는 것만으로 이 목록이 낡아지고, 그러면 다음 사람이 예외를 늘리는
 *  쪽으로 쉽게 도망간다. */
const ALLOWED: Array<[string, string]> = [
  // 언어 이름은 그 언어로 쓴다 — 영어 UI에서도 한국어 선택지는 "한국어"여야
  // 사용자가 찾을 수 있다(브라우저·OS 언어 선택기의 보편적 관례).
  // LANGUAGE_LABEL이 그 표기의 단일 출처다(헤더 배지 + 프로젝트 목록이 쓴다).
  // AppHeader는 이제 그 상수를 임포트하므로 예외가 필요 없다.
  ["lib/i18n/index.ts", "LANGUAGE_LABEL"],
  ["components/CreateProjectForm.tsx", "한국어"],
  ["components/LanguageSwitcher.tsx", "한국어"],
  // 스위치의 aria-label은 두 언어를 함께 담는다 — 어느 언어 사용자든 스크린
  // 리더로 찾을 수 있어야 하고, UI 언어로 번역하면 반대쪽 사용자가 잃는다.
  ["components/LanguageSwitcher.tsx", "Language / 언어"],
  // 감사 로그·문서 본문을 **읽는** 정규식이다. 그 텍스트는 프로젝트 언어로
  // 기록돼 있고 UI 언어와 무관하므로, 두 언어를 다 받아야 한다
  // (parsers/audit.py가 `사용자 입력|User Raw Input`을 둘 다 받는 것과 같은 규율).
  ["components/review/VerificationSummary.tsx", "승인|게이트"],
  ["lib/approvalState.ts", "수정|revise"],
  ["lib/approvalMarker.ts", "승인|Approved"],
  // 사용자가 **채팅으로** 답한 승인을 감사 로그에서 찾는 판정식과, 그것을
  // 승인 게이트 문맥으로 제한하는 판정식. 실측(pilot1의 audit.md)에서 승인
  // 게이트 5건 중 3건이 "동의"/"진행"/"A"였고, 그 표기는 프로젝트 언어로
  // 기록돼 있어 UI 언어와 무관하다.
  ["lib/approvalMarker.ts", "승인|동의|진행"],
  ["lib/approvalMarker.ts", "최종\\s*승인"],
  // 에이전트가 쓴 스테이지 이름을 매칭한다 — 프로젝트 언어로 적혀 있다.
  ["components/workspace/WorkspaceRightPanel.tsx", "프로토타입"],
  // **에이전트에게 보내는** 텍스트다(트랜스크립트에 사용자 말풍선으로 남는다).
  // UI 언어가 아니라 프로젝트 언어를 따른다 — 각 파일 헤더에 근거가 있다.
  ["lib/approvalMarker.ts", 'ko: "승인"'],
  ["lib/startMessage.ts", "ko:"],
  ["app/projects/[projectId]/workspace/page.tsx", "첨부 파일"],
];

function isAllowedLine(rel: string, line: string): boolean {
  return ALLOWED.some(([f, needle]) => rel === f && line.includes(needle));
}

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      if (name === "node_modules" || name === ".next") continue;
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(ts|tsx)$/.test(name)) out.push(full);
    }
  };
  for (const d of SCAN_DIRS) walk(join(ROOT, d));
  return out;
}

/** 주석을 지운 소스. 남은 한글은 코드/JSX에 있는 것이다.
 *
 * 블록 주석과 줄 주석을 지운다. 문자열 안의 "//"까지 주석으로 오인할 수 있는
 * 단순한 방식이지만, 그 방향의 오차는 **거짓 통과가 아니라 거짓 실패**로 가지
 * 않는다(더 많이 지우므로 놓칠 수는 있어도 없는 것을 만들지는 않는다). 그리고
 * 이 리포에서 한국어 주석은 매우 흔하므로, 정확한 파서를 들이는 비용보다 이
 * 근사가 낫다. */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")      // /* ... */ 및 {/* ... */}
    .replace(/^[ \t]*\/\/.*$/gm, "")       // 줄 전체가 주석
    .replace(/[ \t]+\/\/.*$/gm, "");       // 코드 뒤 꼬리 주석
}

describe("UI 소스에 한국어 리터럴이 없다", () => {
  it("every Korean user-facing string lives in the dictionary, not in a component", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const rel = relative(ROOT, file);
      if (isExempt(rel)) continue;
      const body = stripComments(readFileSync(file, "utf8"));
      body.split("\n").forEach((line, i) => {
        if (!HANGUL.test(line)) return;
        if (isAllowedLine(rel, line)) return;
        offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders, offenders.length
      ? `한국어가 소스에 직접 박혀 있다 (${offenders.length}곳). 영어 UI에서 그대로 ` +
        `노출된다 — lib/i18n/{ko,en}.ts에 키를 넣고 t("...")로 바꿔라:\n` +
        offenders.join("\n")
      : "").toEqual([]);
  });
});
