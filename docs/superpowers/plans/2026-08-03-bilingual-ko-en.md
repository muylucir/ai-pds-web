# 한국어/영어 이중 언어 지원 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UI 언어를 사용자별 쿠키로 전환하고, 생성되는 문서·프로토타입·채팅 언어를 프로젝트별 설정으로 결정한다.

**Architecture:** 두 개의 독립된 언어 채널. ① UI 언어 = `pf_lang` 쿠키 → `app/layout.tsx`가 읽어 `LocaleProvider`에 내려줌 → 모든 컴포넌트가 `useT()`. ② 생성물 언어 = `project.json` 매니페스트 → `ProjectRegistry.get_language()` → `place_rules`가 워크스페이스 `CLAUDE.md`를 언어별로 조립 + 프로토타입/설문 프롬프트가 언어별 판을 고름. 공유 `CLAUDE_CONFIG_DIR`에서는 언어 지시를 전부 제거한다 — 두 레벨에 언어 지시가 있으면 어느 쪽이 이길지 예측할 수 없다(커밋 `7f33652`가 겪은 실패).

**Tech Stack:** Next.js 15 (App Router), React 19, TypeScript, Tailwind, vitest + @testing-library/react, msw / FastAPI, Python 3.11, pytest, pytest-asyncio

## Global Constraints

- **비ASCII 리터럴 표기:** 툴 호출 파라미터(JSON)의 한글 등 비ASCII 문자열은 항상 리터럴 UTF-8로 쓴다. `\uXXXX` 유니코드 이스케이프를 쓰지 않는다.
- **로케일 값은 정확히 두 개:** `"ko"` | `"en"`. 그 외 문자열은 백엔드가 400으로 거절한다.
- **기본 로케일은 `ko`.** 쿠키 없음 / Provider 밖 / 매니페스트에 `language` 없음 → 모두 `ko`. 이것이 기존 테스트 535건을 그대로 통과시키는 장치다.
- **쿠키 이름은 `pf_lang`**, httpOnly 아님, `path=/`, `sameSite=lax`.
- **기존 한국어 데이터 호환은 필수:** 감사 로그의 `승인`, 트랜스크립트의 `사용자 답변: ` 접두사, `language` 없는 구 매니페스트 — 셋 다 계속 동작해야 한다.
- **테스트 베이스라인:** 프론트엔드 `83개 파일 / 664개 테스트` 전부 통과. 각 태스크 후 이 수가 줄어들면 안 된다(늘어나는 것은 정상).
- **한국어 문자열을 직접 쓰는 기존 단정문은 고치지 않는다.** 딕셔너리 조회로 바꾸면 "딕셔너리가 자기 자신과 같다"는 무의미한 테스트가 된다.
- **프롬프트는 조립하지 않는다.** 언어별로 완성된 문장 두 벌을 유지한다 — 문장을 쪼개면 지시의 강도가 어느 언어에서 약해졌는지 알 수 없다.

## 파일 구조

**신규 (프론트엔드)**
| 파일 | 책임 |
|---|---|
| `frontend/lib/i18n/index.ts` | `Locale` 타입, `DEFAULT_LOCALE`, `LANG_COOKIE`, `isLocale()`, `dictFor()` |
| `frontend/lib/i18n/ko.ts` | 한국어 딕셔너리 (평면 키) |
| `frontend/lib/i18n/en.ts` | 영어 딕셔너리 — 타입으로 `ko`와 키 집합 동일 강제 |
| `frontend/lib/i18n/provider.tsx` | `LocaleProvider`, `useT()`, `useLocale()` |
| `frontend/components/LanguageSwitcher.tsx` | 쿠키 쓰기 + `router.refresh()` |
| `frontend/lib/api/errorMessage.ts` | 백엔드 에러 코드 → UI 문구 (모르는 코드는 원문 폴백) |
| `frontend/lib/approvalMarker.ts` | 승인 턴 텍스트와 판정 정규식의 단일 출처 |

**신규 (백엔드/룰)**
| 파일 | 책임 |
|---|---|
| `rule/aiplc-rules/language/ko.md` | 한국어 지시 + 상류 템플릿 번역 오버라이드 |
| `rule/aiplc-rules/language/en.md` | 영어 지시 (번역 오버라이드 없음) |
| `backend/pathfinder/error_codes.py` | HTTP `detail`로 나가는 안정적 코드 상수 |
| `backend/pathfinder/proto/prompts.py` | 프로토타입 개시 프롬프트 4종 × 2언어 |
| `backend/pathfinder/survey/report_labels.py` | 설문 리포트 마크다운 라벨 × 2언어 |

**주요 수정**
| 파일 | 변경 |
|---|---|
| `frontend/app/layout.tsx` | `cookies()`로 로케일 읽기, `<html lang>`, `LocaleProvider` |
| `frontend/components/AppHeader.tsx` | `useT()`, `LanguageSwitcher`, 언어 배지 |
| `backend/pathfinder/project_store.py` | `write_manifest(language=)`, `restore_projects` 5-tuple |
| `backend/pathfinder/workspace.py` | `ProjectRegistry._language`, `get_language()` |
| `backend/pathfinder/agent/workspace_rules.py` | `place_rules(..., language)` 조립 |
| `backend/pathfinder/proto/session.py` | 프롬프트를 `prompts.py`에 위임 |
| `rule/aiplc-rules/aws-aiplc-rules/core-workflow.md` | 3행 언어 헤더 삭제 |
| `discovery-config/CLAUDE.md` | §번역 오버라이드 삭제 (비ASCII 규약은 남김) |
| `proto-config/CLAUDE.md` | 1행 언어 지시 삭제 |

---

## Task 1: i18n 코어 — 타입, 딕셔너리, Provider

**Files:**
- Create: `frontend/lib/i18n/index.ts`
- Create: `frontend/lib/i18n/ko.ts`
- Create: `frontend/lib/i18n/en.ts`
- Create: `frontend/lib/i18n/provider.tsx`
- Test: `frontend/lib/i18n/index.test.ts`, `frontend/lib/i18n/provider.test.tsx`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `type Locale = "ko" | "en"`
  - `const DEFAULT_LOCALE: Locale = "ko"`
  - `const LANG_COOKIE = "pf_lang"`
  - `function isLocale(v: unknown): v is Locale`
  - `type Dict = Record<keyof typeof ko, string>`
  - `function dictFor(locale: Locale): Dict`
  - `<LocaleProvider locale={Locale}>{children}</LocaleProvider>`
  - `function useT(): (key: keyof Dict) => string`
  - `function useLocale(): Locale`

이 태스크는 **딕셔너리에 키를 2개만 넣는다** (`nav.dashboard`, `nav.workspace`). 나머지 키는 Task 8이 화면을 치환하며 함께 추가한다 — 쓰이지 않는 키를 미리 400개 만들면 어느 것이 실제로 연결됐는지 알 수 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `index.ts`**

`frontend/lib/i18n/index.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { DEFAULT_LOCALE, LANG_COOKIE, isLocale, dictFor } from "./index";
import { ko } from "./ko";
import { en } from "./en";

describe("locale basics", () => {
  it("기본 로케일은 ko다 — 쿠키가 없는 기존 사용자와 테스트가 현재 화면을 그대로 본다", () => {
    expect(DEFAULT_LOCALE).toBe("ko");
  });

  it("쿠키 이름을 한 곳에서만 정한다", () => {
    expect(LANG_COOKIE).toBe("pf_lang");
  });

  it("isLocale은 두 값만 통과시킨다", () => {
    expect(isLocale("ko")).toBe(true);
    expect(isLocale("en")).toBe(true);
    expect(isLocale("ja")).toBe(false);
    expect(isLocale("")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
    expect(isLocale(null)).toBe(false);
    expect(isLocale(5)).toBe(false);
  });

  it("dictFor가 로케일별 딕셔너리를 준다", () => {
    expect(dictFor("ko")).toBe(ko);
    expect(dictFor("en")).toBe(en);
  });
});

describe("dictionary key parity", () => {
  // en.ts는 타입으로 ko.ts의 키 집합을 강제받지만(Record<keyof typeof ko, string>),
  // 그 강제는 컴파일 시점이고 vitest는 타입을 검사하지 않는다. 이 테스트가
  // 런타임 회귀 방지다 — 키가 어긋나면 화면에 undefined가 뜬다.
  it("두 딕셔너리의 키 집합이 정확히 같다", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ko).sort());
  });

  it("어느 값도 비어 있지 않다", () => {
    for (const [k, v] of Object.entries({ ...ko, ...en })) {
      expect(v.trim(), `빈 값: ${k}`).not.toBe("");
    }
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/i18n/index.test.ts`
Expected: FAIL — `Failed to resolve import "./index"`

- [ ] **Step 3: 딕셔너리 두 개를 만든다**

`frontend/lib/i18n/ko.ts`:

```typescript
// frontend/lib/i18n/ko.ts — 한국어 UI 문자열.
//
// 키는 평면이다(중첩 객체 없음). 중첩을 쓰면 `t("a.b.c")` 형태의 문자열 경로가
// 되어 타입 검사를 잃는다 — 평면 키는 `keyof typeof ko`로 오타가 컴파일 에러가
// 된다.
//
// 키 이름은 `영역.용도` 규약이다(`nav.dashboard`, `header.modelBadgeTitle`).
// 화면 문자열을 치환할 때마다 여기에 키를 추가하고, en.ts에도 같은 키를 넣는다 —
// 타입이 강제하므로 빠뜨리면 컴파일이 실패한다.
export const ko = {
  "nav.dashboard": "대시보드",
  "nav.workspace": "워크스페이스",
} as const;
```

`frontend/lib/i18n/en.ts`:

```typescript
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
};
```

`frontend/lib/i18n/index.ts`:

```typescript
// frontend/lib/i18n/index.ts — 로케일 타입과 딕셔너리 조회의 단일 출처.
//
// URL 세그먼트 라우팅(/ko/..., /en/...)을 쓰지 않는다. 그러면 middleware.ts의
// gateDecision, lib/auth/safeNext.ts, lib/api/rewriteLocation.ts, 그리고
// /api/proto/{pid}/{slug}/ 프록시 프리픽스가 전부 로케일 세그먼트를 다뤄야
// 한다. trailingSlash/basePath 리다이렉트 루프를 이미 겪은 프록시 계층을 언어
// 때문에 다시 건드릴 이유가 없다. 쿠키 기반, 경로 불변.
import { ko } from "./ko";
import { en } from "./en";

export type Locale = "ko" | "en";

// 쿠키 없음 / Provider 밖 / 알 수 없는 값 → 전부 이 값. 기존 사용자와 기존
// 테스트 535건이 현재 화면을 그대로 보는 것이 이 기본값에 달려 있다.
export const DEFAULT_LOCALE: Locale = "ko";

// httpOnly가 아니다 — LanguageSwitcher가 클라이언트에서 써야 하고, 보안 값이
// 아니다. app/layout.tsx가 서버에서 읽어 <html lang>을 첫 페인트에 맞춘다.
export const LANG_COOKIE = "pf_lang";

export type Dict = Record<keyof typeof ko, string>;

export function isLocale(value: unknown): value is Locale {
  return value === "ko" || value === "en";
}

export function dictFor(locale: Locale): Dict {
  return locale === "en" ? en : ko;
}

export { ko, en };
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/i18n/index.test.ts`
Expected: PASS (6 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — Provider**

`frontend/lib/i18n/provider.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LocaleProvider, useT, useLocale } from "./provider";

function Probe() {
  const t = useT();
  const locale = useLocale();
  return <p data-testid="out">{`${locale}:${t("nav.dashboard")}`}</p>;
}

describe("LocaleProvider", () => {
  it("Provider의 로케일로 번역한다", () => {
    render(
      <LocaleProvider locale="en">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("en:Dashboard");
  });

  it("ko Provider는 한국어를 준다", () => {
    render(
      <LocaleProvider locale="ko">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("ko:대시보드");
  });
});

describe("Provider 밖에서의 폴백", () => {
  // 이것이 기존 테스트 535건을 그대로 통과시키는 장치다. 그 테스트들은
  // 컴포넌트를 Provider로 감싸지 않고 render()하므로, 훅이 던지면 전부 깨진다.
  it("Provider 없이도 ko로 동작한다 (던지지 않는다)", () => {
    render(<Probe />);
    expect(screen.getByTestId("out")).toHaveTextContent("ko:대시보드");
  });
});
```

- [ ] **Step 6: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/i18n/provider.test.tsx`
Expected: FAIL — `Failed to resolve import "./provider"`

- [ ] **Step 7: Provider를 구현한다**

`frontend/lib/i18n/provider.tsx`:

```typescript
"use client";
// frontend/lib/i18n/provider.tsx — UI 로케일을 컴포넌트 트리에 내려준다.
//
// 서버용 경로(getT())가 없는 이유: 이 앱에서 서버에서 렌더되는 것은
// app/layout.tsx와 redirect()만 하는 두 페이지뿐이고, 그 셋에는 UI 문자열이
// 없다. `"use client"`가 없는 컴포넌트가 26개 있지만(AppHeader 등) 전부
// 클라이언트 페이지 트리 아래에서만 임포트되므로 이미 클라이언트 컴포넌트다 —
// Next.js는 클라이언트 컴포넌트가 임포트한 것을 클라이언트 번들에 넣는다.
import { createContext, useContext, useMemo } from "react";

import { DEFAULT_LOCALE, dictFor, type Dict, type Locale } from "./index";

// null이 "Provider 밖"을 뜻한다. 기본값을 DEFAULT_LOCALE로 두지 않는 이유는
// 없지만(결과가 같다), null이 의도를 드러낸다 — 폴백이 일어났다는 사실이
// 컨텍스트 값에 남는다.
const LocaleContext = createContext<Locale | null>(null);

export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: React.ReactNode;
}) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

/** 현재 UI 로케일. Provider 밖에서는 DEFAULT_LOCALE. */
export function useLocale(): Locale {
  return useContext(LocaleContext) ?? DEFAULT_LOCALE;
}

/**
 * 번역 함수.
 *
 * **Provider 밖에서 던지지 않는다.** 기존 컴포넌트 테스트 535건이 Provider로
 * 감싸지 않고 render()하므로, 던지면 그 전부가 깨진다. 폴백은 DEFAULT_LOCALE
 * (=ko)이고, 그것이 그 테스트들이 단정하는 화면이다.
 */
export function useT(): (key: keyof Dict) => string {
  const locale = useLocale();
  // 로케일이 바뀌지 않는 한 같은 함수를 돌려준다 — 이 함수를 의존성 배열에
  // 넣는 useEffect/useMemo가 매 렌더마다 다시 돌지 않게 한다.
  return useMemo(() => {
    const dict = dictFor(locale);
    return (key: keyof Dict) => dict[key];
  }, [locale]);
}
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/i18n/`
Expected: PASS (9 tests)

- [ ] **Step 9: 전체 스위트로 회귀가 없음을 확인한다**

Run: `cd frontend && npx vitest run 2>&1 | tail -5`
Expected: `Test Files 85 passed (85)` / `Tests 673 passed (673)` — 기존 83파일 664테스트 + 신규 2파일 9테스트. 기존 수가 줄면 안 된다.

- [ ] **Step 10: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/i18n/
git commit -m "feat(i18n): 로케일 타입·딕셔너리·Provider

useT()는 Provider 밖에서 던지지 않고 ko로 폴백한다 — 기존 컴포넌트 테스트가
Provider로 감싸지 않고 render()하므로 이 폴백이 회귀 방지다."
```

---

## Task 2: `layout.tsx` 배관 + `LanguageSwitcher`

**Files:**
- Modify: `frontend/app/layout.tsx`
- Create: `frontend/components/LanguageSwitcher.tsx`
- Test: `frontend/components/LanguageSwitcher.test.tsx`

**Interfaces:**
- Consumes: `LANG_COOKIE`, `DEFAULT_LOCALE`, `isLocale`, `Locale` (Task 1); `LocaleProvider`, `useLocale` (Task 1)
- Produces: `<LanguageSwitcher />` — props 없음. `AppHeader`(Task 3)가 이것을 꽂는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/components/LanguageSwitcher.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

import { LanguageSwitcher } from "./LanguageSwitcher";
import { LocaleProvider } from "@/lib/i18n/provider";

beforeEach(() => {
  refresh.mockClear();
  // 쿠키는 jsdom 문서에 남으므로 테스트 간 지운다.
  document.cookie = "pf_lang=; max-age=0; path=/";
});

afterEach(() => {
  document.cookie = "pf_lang=; max-age=0; path=/";
});

describe("LanguageSwitcher", () => {
  it("두 언어를 버튼으로 보여준다", () => {
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    expect(screen.getByRole("button", { name: "한국어" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
  });

  it("현재 로케일을 aria-pressed로 표시한다", () => {
    render(
      <LocaleProvider locale="en">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    expect(screen.getByRole("button", { name: "English" }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "한국어" }))
      .toHaveAttribute("aria-pressed", "false");
  });

  it("클릭하면 쿠키를 쓰고 refresh를 부른다", async () => {
    const user = userEvent.setup();
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(document.cookie).toContain("pf_lang=en");
    // refresh가 없으면 layout.tsx가 다시 렌더되지 않아 <html lang>과 Provider
    // 초기값이 그대로 남는다 — 쿠키만 바뀌고 화면은 안 바뀐다.
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("이미 그 언어면 아무것도 하지 않는다", async () => {
    const user = userEvent.setup();
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    await user.click(screen.getByRole("button", { name: "한국어" }));
    expect(refresh).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/LanguageSwitcher.test.tsx`
Expected: FAIL — `Failed to resolve import "./LanguageSwitcher"`

- [ ] **Step 3: `LanguageSwitcher`를 구현한다**

`frontend/components/LanguageSwitcher.tsx`:

```typescript
"use client";
// frontend/components/LanguageSwitcher.tsx — 헤더의 UI 언어 전환.
//
// AppHeader에서 분리한 이유: AppHeader 자체도 클라이언트 컴포넌트지만, 쿠키
// 쓰기와 router.refresh()는 별개 책임이다(UserMenu가 같은 형태로 분리돼 있다).
//
// 라벨은 번역하지 않는다 — 언어 선택지는 항상 그 언어 자체로 표기한다.
// "영어"라고 쓰면 한국어를 모르는 사용자가 자기 언어를 찾을 수 없다.
import { useRouter } from "next/navigation";

import { LANG_COOKIE, type Locale } from "@/lib/i18n";
import { useLocale } from "@/lib/i18n/provider";

const OPTIONS: Array<{ locale: Locale; label: string }> = [
  { locale: "ko", label: "한국어" },
  { locale: "en", label: "English" },
];

// 1년. 세션 쿠키로 두면 브라우저를 닫을 때마다 한국어로 돌아가고, 그것은
// 사용자가 선택을 다시 하게 만든다.
const MAX_AGE = 60 * 60 * 24 * 365;

export function LanguageSwitcher() {
  const current = useLocale();
  const router = useRouter();

  function choose(locale: Locale) {
    // 같은 언어를 다시 고르는 것은 no-op. refresh()를 부르면 서버 왕복이
    // 일어나므로 아무 변화 없는 요청을 만들지 않는다.
    if (locale === current) return;
    // httpOnly가 아니므로 여기서 쓸 수 있다. secure를 붙이지 않는 이유는
    // 로컬 http 개발에서 쿠키가 저장되지 않게 되기 때문이다 — 이 값은
    // 자격증명이 아니다.
    document.cookie =
      `${LANG_COOKIE}=${locale}; path=/; max-age=${MAX_AGE}; samesite=lax`;
    // 쿠키만 쓰고 끝내면 화면이 그대로다: <html lang>과 LocaleProvider의
    // 초기값은 app/layout.tsx가 서버에서 정하므로, 그것을 다시 렌더해야 한다.
    router.refresh();
  }

  return (
    <div
      className="hidden sm:inline-flex items-center rounded-full border border-slate-200 p-0.5"
      role="group"
      aria-label="Language / 언어"
    >
      {OPTIONS.map(({ locale, label }) => {
        const active = locale === current;
        return (
          <button
            key={locale}
            type="button"
            onClick={() => choose(locale)}
            aria-pressed={active}
            className={`px-2.5 py-1 text-xs rounded-full ${
              active
                ? "bg-violet-600 text-white font-medium"
                : "text-slate-500 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/LanguageSwitcher.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 5: `layout.tsx`를 고친다**

`frontend/app/layout.tsx` 전문을 아래로 교체한다:

```typescript
import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";

import { DEFAULT_LOCALE, isLocale, LANG_COOKIE } from "@/lib/i18n";
import { LocaleProvider } from "@/lib/i18n/provider";

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-noto-sans-kr",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pathfinder",
  description: "AI-PLC Discovery 웹 서비스",
};

// 이 앱에서 cookies()를 부르는 유일한 지점이다. 로케일을 서버에서 읽는 이유는
// <html lang>을 첫 페인트에 맞추기 위해서다 — localStorage는 서버에서 보이지
// 않아 한국어로 그린 뒤 영어로 바뀌는 깜빡임이 생긴다.
//
// async가 된 것에 주의: Next 15의 cookies()는 Promise를 돌려준다.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const raw = (await cookies()).get(LANG_COOKIE)?.value;
  // 알 수 없는 값(손으로 고친 쿠키, 옛 값)은 조용히 기본값으로 떨어진다.
  // 던지면 모든 페이지가 500이 되는데, 언어 하나 때문에 그럴 이유가 없다.
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return (
    <html lang={locale} className={notoSansKr.variable}>
      <body className="font-sans">
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 6: 빌드로 서버/클라이언트 경계를 검증한다**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -20`
Expected: 타입 에러 없음, 빌드 성공. **`cookies()`를 부르는 컴포넌트가 클라이언트 번들에 들어갔다면 여기서 실패한다** — 이 태스크에서 그 경계가 맞는지 확인하는 유일한 방법이다(vitest는 Next의 경계를 모른다).

- [ ] **Step 7: 전체 스위트로 회귀가 없음을 확인한다**

Run: `cd frontend && npx vitest run 2>&1 | tail -5`
Expected: `Test Files 86 passed (86)` / `Tests 677 passed (677)`. 기존 664가 줄지 않았는지 확인한다.

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/app/layout.tsx frontend/components/LanguageSwitcher.tsx frontend/components/LanguageSwitcher.test.tsx
git commit -m "feat(i18n): layout에서 쿠키로 로케일 판독 + 언어 스위치

layout.tsx가 cookies()를 부르는 유일한 지점이다 — <html lang>을 첫 페인트에
맞추려면 서버가 알아야 한다. 스위치는 쿠키를 쓰고 router.refresh()로 그
판독을 다시 돌린다."
```

---

## Task 3: 생성물 언어 — 매니페스트 → 레지스트리 → 라우트

**Files:**
- Modify: `backend/pathfinder/project_store.py:18-61`
- Modify: `backend/pathfinder/workspace.py:58-125`
- Modify: `backend/pathfinder/routes/projects.py:37-127`
- Modify: `backend/pathfinder/app.py:420-421`
- Test: `backend/tests/test_project_store.py`, `backend/tests/test_registry.py`, `backend/tests/test_routes_projects_language.py` (신규)

**Interfaces:**
- Consumes: 없음 (백엔드 첫 태스크)
- Produces:
  - `write_manifest(root, project_id, name, created_at=None, model_id=None, language=None) -> str`
  - `restore_projects(root) -> list[tuple[str, str|None, str|None, str|None, str|None]]` — 5-tuple `(pid, name, created_at, model_id, language)`
  - `ProjectRegistry.register(pid, name=None, created_at=None, model_id=None, language=None)`
  - `ProjectRegistry.get_language(pid) -> str` — 항상 `"ko"` 또는 `"en"`, 미등록/미지정은 `"ko"`
  - `app_module.project_language(project_id: str) -> str`
  - `POST /projects` 바디에 `language: str | None`, 응답에 `"language"`
  - `GET /projects/{pid}` 응답에 `"language"`, `GET /projects` 각 행에 `"language"`

**베이스라인:** `cd backend && .venv/bin/python -m pytest -q` → `878 passed`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `project_store`**

`backend/tests/test_project_store.py`의 기존 `test_restore_reads_manifests_and_skips_garbage`를 아래로 **교체**하고(5-tuple로 언패킹이 바뀌므로 그대로 두면 ValueError), 그 뒤에 새 테스트 2개를 **추가**한다:

```python
@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps(
        {"project_id": "pa", "name": "A", "created_at": "2026-07-22T01:00:00+00:00",
         "model_id": "global.anthropic.claude-opus-5", "language": "en"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # 손상 → 건너뜀
    root.blobs["pd/project.json"] = "[1,2,3]"                # JSON but not dict → 건너뜀
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # 매니페스트 아님 → 무시
    restored = {pid: (name, created_at, model_id, language)
                for pid, name, created_at, model_id, language
                in await restore_projects(root)}
    # created_at·model_id·language는 매니페스트에서 승계, 없으면(구 매니페스트) None.
    assert restored == {
        "pa": ("A", "2026-07-22T01:00:00+00:00",
               "global.anthropic.claude-opus-5", "en"),
        "pb": (None, None, None, None),
    }


@pytest.mark.asyncio
async def test_write_manifest_records_the_language():
    root = FakeS3Store()
    await write_manifest(root, "p1", None, language="en")
    assert json.loads(root.blobs["p1/project.json"])["language"] == "en"


@pytest.mark.asyncio
async def test_write_manifest_records_an_unset_language_as_explicit_null():
    # 키를 빼면 '구 매니페스트'와 '언어를 고르지 않은 새 프로젝트'를 구별할 수
    # 없다 — model_id와 같은 판단이다.
    root = FakeS3Store()
    await write_manifest(root, "p1", None)
    assert json.loads(root.blobs["p1/project.json"])["language"] is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_store.py -q`
Expected: FAIL — `test_write_manifest_records_the_language`가 `TypeError: write_manifest() got an unexpected keyword argument 'language'`, restore 테스트는 `ValueError: not enough values to unpack (expected 5, got 4)`

- [ ] **Step 3: `project_store.py`를 고친다**

`write_manifest`의 시그니처와 본문(18-35행):

```python
async def write_manifest(root: S3StoreLike, project_id: str, name: str | None,
                         created_at: str | None = None,
                         model_id: str | None = None,
                         language: str | None = None) -> str:
    """매니페스트를 쓰고 기록된 created_at을 반환한다 — 호출부(생성 라우트)가
    같은 시각을 레지스트리에도 등록해 목록 정렬 기준을 일치시킨다.

    model_id는 카탈로그를 참조하지 않고 **복사**한다: 관리자가 그 모델을
    카탈로그에서 지워도 이 프로젝트는 계속 같은 모델로 돌아야 한다. 미지정은
    명시적 null로 기록한다 — 키를 빼면 '구 매니페스트'와 '모델을 고르지 않은
    새 프로젝트'를 구별할 수 없다.

    language("ko"|"en")는 이 프로젝트의 **생성물 언어**다 — 문서·프로토타입·
    채팅이 어느 언어로 나오는지. UI 언어와 별개이고(그쪽은 사용자별 쿠키),
    생성 시점 1회 결정이다: 진행 중에 바꾸면 이미 만들어진 aiplc-docs/**와
    트랜스크립트가 이전 언어로 남아 한 프로젝트 안에서 문서 언어가 섞인다.
    model_id와 같은 이유로 미지정도 명시적 null로 기록한다.
    """
    ts = created_at or datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {"project_id": project_id, "name": name, "created_at": ts,
         "model_id": model_id, "language": language},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)
    return ts
```

`restore_projects`(38-61행)를 5-tuple로:

```python
async def restore_projects(
    root: S3StoreLike,
) -> list[tuple[str, str | None, str | None, str | None, str | None]]:
    """projects/ 스캔 → 매니페스트 병렬 GET →
    [(pid, name, created_at, model_id, language)].
    손상 항목은 로그 후 건너뜀 — 하나가 썩어도 나머지 복원을 막지 않는다.
    created_at·model_id·language는 구 매니페스트에 없을 수 있어 None 허용
    (정렬 시 맨 앞, 모델은 env 폴백, 언어는 'ko' 폴백 —
    ProjectRegistry.get_language가 확정한다)."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    for key, body in zip(keys, bodies):
        if isinstance(body, BaseException):
            _log.warning("manifest read failed for %s: %r", key, body)
            continue
        try:
            d = json.loads(body)
            if not isinstance(d, dict):
                _log.warning("corrupt manifest skipped: %s", key)
                continue
            pid = d.get("project_id") or _MANIFEST.match(key).group(1)  # type: ignore[union-attr]
            out.append((pid, d.get("name"), d.get("created_at"),
                        d.get("model_id"), d.get("language")))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_store.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — 레지스트리**

`backend/tests/test_registry.py` 끝에 추가:

```python
def test_register_stores_and_returns_language():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", language="en")
    assert r.get_language("p1") == "en"


def test_get_language_is_ko_for_a_project_registered_without_one():
    # 구 매니페스트로 복원된 프로젝트는 전부 한국어로 만들어진 것이므로,
    # None을 ko로 읽는 것이 사실에 맞다.
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_language("p1") == "ko"


def test_get_language_is_ko_for_an_unknown_project():
    # get_model_id가 None을 돌려주는 것과 다른 선택이다: 언어에는 "없음"이라는
    # 유효 상태가 없다(어떤 언어로든 써야 한다). 호출부가 폴백을 반복하지 않게
    # 레지스트리가 확정한다.
    from pathfinder.workspace import ProjectRegistry
    assert ProjectRegistry().get_language("nope") == "ko"


def test_get_language_falls_back_for_a_junk_value():
    # 손상된 매니페스트가 임의 문자열을 실어 와도 place_rules가 어느 지시
    # 블록을 붙일지 결정할 수 있어야 한다.
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="klingon")
    assert r.get_language("p1") == "ko"


def test_remove_drops_the_language():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="en")
    r.remove("p1")
    assert r.get_language("p1") == "ko"
```

- [ ] **Step 6: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py -q`
Expected: FAIL — `TypeError: register() got an unexpected keyword argument 'language'`

- [ ] **Step 7: `workspace.py`의 `ProjectRegistry`를 고친다**

`__init__`의 끝(73행 뒤)에 추가:

```python
        # 이 프로젝트의 생성물 언어("ko"|"en"). model_id와 같은 규율로
        # 매니페스트에서 복사돼 온다. None = 미지정(구 매니페스트 포함) —
        # get_language가 "ko"로 확정한다.
        self._language: dict[str, str | None] = {}
```

`register`를 교체:

```python
    def register(self, project_id: str, name: str | None = None,
                 created_at: str | None = None,
                 model_id: str | None = None,
                 language: str | None = None) -> None:
        self._names[project_id] = name
        self._created_at[project_id] = created_at
        self._model_id[project_id] = model_id
        self._language[project_id] = language
```

`remove`에 한 줄 추가(101행 뒤):

```python
        self._language.pop(project_id, None)
```

파일 끝에 `get_language`를 추가:

```python
    #: 생성물 언어의 허용값. place_rules가 이 값으로 언어별 지시 블록을 고르므로
    #: 그 밖의 값은 존재할 수 없다.
    _LANGUAGES = ("ko", "en")

    def get_language(self, project_id: str) -> str:
        """이 프로젝트의 생성물 언어. **항상 "ko" 또는 "en"을 돌려준다.**

        get_model_id가 None을 돌려주는 것과 다른 선택이다: 언어에는 "없음"이라는
        유효 상태가 없다 — 문서는 어떤 언어로든 써야 한다. 호출부(place_rules,
        프로토타입 프롬프트, 설문 리포트)가 각자 폴백을 반복하면 그중 하나가
        빠뜨렸을 때 조용히 다른 언어가 나오므로, 여기서 확정한다.

        폴백이 "ko"인 이유는 이 기능 이전에 만든 프로젝트가 전부 한국어로
        만들어졌기 때문이다 — 구 매니페스트에는 language 키가 없다.

        알 수 없는 값도 "ko"로 떨어진다. 라우트가 생성 시점에 검증하므로
        정상 경로로는 들어올 수 없지만, 손상된 매니페스트가 임의 문자열을
        실어 오면 던지는 것보다 한국어로 도는 편이 낫다.
        """
        value = self._language.get(project_id)
        return value if value in self._LANGUAGES else "ko"
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py -q`
Expected: PASS (14 tests)

- [ ] **Step 9: 실패하는 테스트를 쓴다 — 라우트**

`backend/tests/test_routes_projects_language.py` (신규):

```python
# backend/tests/test_routes_projects_language.py
#
# 생성 시점의 language 검증과 조회. model_id와 같은 배관을 쓰지만 검증 기준이
# 다르다 — 카탈로그가 아니라 고정된 두 값이다.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pl-1", "pl-2", "pl-3", "pl-4", "pl-5"):
        app_module.registry.remove(pid)


def test_create_accepts_en_and_records_it(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert json.loads(fake.blobs["pl-1/project.json"])["language"] == "en"
    assert app_module.registry.get_language("pl-1") == "en"


def test_create_without_a_language_defaults_to_ko(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-2"})
    assert r.status_code == 200
    # 응답은 실제로 돌게 될 언어를 말한다 — 미지정을 null로 돌려주면 프론트가
    # 폴백 규칙을 또 알아야 한다.
    assert r.json()["language"] == "ko"
    assert app_module.registry.get_language("pl-2") == "ko"


def test_create_rejects_an_unknown_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-3", "language": "ja"})
    assert r.status_code == 400
    assert not app_module.registry.is_registered("pl-3")


def test_get_project_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    app_module.registry.register("pl-4", "이름",
                                 created_at="2026-08-03T00:00:00+00:00",
                                 language="en")
    body = client.get("/projects/pl-4").json()
    assert body["language"] == "en"


def test_list_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pl-5", "language": "en"})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pl-5")
    assert row["language"] == "en"


def test_project_language_helper_reads_the_registry():
    app_module.registry.register("pl-1", None, language="en")
    assert app_module.project_language("pl-1") == "en"
    # 미등록도 ko — 레지스트리가 확정하는 것을 그대로 통과시킨다.
    assert app_module.project_language("never-existed") == "ko"
```

- [ ] **Step 10: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_language.py -q`
Expected: FAIL — `KeyError: 'language'` / `AttributeError: module 'pathfinder.app' has no attribute 'project_language'`

- [ ] **Step 11: 라우트와 `app.py`를 고친다**

`backend/pathfinder/routes/projects.py`의 `CreateProject`(37-42행)에 필드 추가:

```python
class CreateProject(BaseModel):
    project_id: str
    name: str | None = None
    # 이 프로젝트가 쓸 Bedrock 모델 id. 미지정이면 env 기본값으로 돈다
    # (app.project_model의 폴백 체인).
    model_id: str | None = None
    # 이 프로젝트의 생성물 언어("ko"|"en"). 미지정이면 "ko"로 돈다.
    # UI 언어(pf_lang 쿠키)와 별개다 — 이쪽은 문서·프로토타입·채팅의 언어이고
    # 생성 시점 1회 결정이다.
    language: str | None = None
```

`_validate_model_id` 뒤에 검증 함수를 추가:

```python
#: 허용되는 생성물 언어. ProjectRegistry._LANGUAGES와 같은 집합이어야 한다 —
#: 여기서 통과시킨 값이 그쪽 폴백에 걸리면 사용자가 고른 언어가 조용히
#: 무시된다.
_LANGUAGES = ("ko", "en")


def _validate_language(language: str | None) -> None:
    """두 값만 허용한다.

    임의 문자열이 매니페스트에 들어가면 place_rules가 어느 지시 블록을 붙일지
    결정할 수 없고, ProjectRegistry.get_language가 "ko"로 떨어뜨린다 — 즉
    사용자가 고른 언어가 조용히 무시된다. 생성 시점에 막는 것이 그 침묵을
    없애는 유일한 자리다.
    """
    if language is None:
        return
    if language not in _LANGUAGES:
        raise HTTPException(status_code=400,
                            detail="지원하지 않는 언어입니다.")
```

`create_project`에서 검증과 전달을 추가한다. `await _validate_model_id(...)` 다음 줄에:

```python
    _validate_language(body.language)
```

`write_manifest(...)` 호출에 `language=body.language`를 추가하고, `registry.register(...)`에도 `language=body.language`를 추가한다. 반환값을 교체:

```python
    return {"project_id": body.project_id, "name": body.name,
            "model_id": body.model_id,
            # 실제로 돌게 될 언어를 돌려준다(미지정 → "ko"). null을 돌려주면
            # 프론트가 폴백 규칙을 또 알아야 한다.
            "language": app_module.registry.get_language(body.project_id)}
```

`list_projects`의 각 행 dict에 추가:

```python
             "language": app_module.registry.get_language(pid),
```

`get_project`의 반환 dict에 추가:

```python
            "language": app_module.registry.get_language(pid)}
```

`backend/pathfinder/app.py`의 `project_model` 바로 뒤(134행 근처)에 추가:

```python
def project_language(project_id: str) -> str:
    """이 프로젝트의 생성물 언어("ko"|"en"). 항상 값이 있다.

    project_model과 달리 env 폴백이 없다: 언어는 프로세스 전역 기본값을 가질
    이유가 없고(모델은 배포가 정하는 것이 자연스럽지만 언어는 프로젝트의
    성질이다), 레지스트리가 이미 "ko"로 확정한다.

    이 함수를 두는 이유는 호출부(driver_factory, proto_session_factory,
    survey_store_factory)가 registry를 직접 만지지 않게 하는 것이다 —
    project_model과 같은 모양을 유지한다.
    """
    return registry.get_language(project_id)
```

`_lifespan`의 복원 루프(420-421행)를 5-tuple로:

```python
            for pid, name, created_at, model_id, language in await restore_projects(
                    projects_root_s3_factory()):
                registry.register(pid, name, created_at=created_at,
                                  model_id=model_id, language=language)
```

- [ ] **Step 12: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_language.py -q`
Expected: PASS (6 tests)

- [ ] **Step 13: 백엔드 전체로 회귀가 없음을 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `891 passed` — 기존 878 + 신규 13. `restore_projects`를 언패킹하는 다른 테스트(`test_app_lifespan_restore.py`)가 깨지면 5-tuple로 함께 고친다.

- [ ] **Step 14: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/project_store.py backend/pathfinder/workspace.py \
        backend/pathfinder/routes/projects.py backend/pathfinder/app.py \
        backend/tests/test_project_store.py backend/tests/test_registry.py \
        backend/tests/test_routes_projects_language.py
git commit -m "feat(language): 프로젝트별 생성물 언어를 매니페스트에 복사

model_id와 같은 배관(매니페스트 → 레지스트리 → 라우트)을 쓰되 폴백이 다르다:
get_language는 항상 ko/en 중 하나를 돌려준다 — 언어에는 '없음'이라는 유효
상태가 없어서, 호출부마다 폴백을 반복하면 하나가 빠뜨렸을 때 조용히 다른
언어가 나온다."
```

---

## Task 4: 헤더 — `useT()`, 언어 스위치, 언어 배지

**Files:**
- Modify: `frontend/components/AppHeader.tsx`
- Modify: `frontend/lib/i18n/ko.ts`, `frontend/lib/i18n/en.ts` (헤더 키 추가)
- Modify: `frontend/lib/api/types.ts` (`ProjectSummary`/`ProjectDetail`에 `language`)
- Modify: `frontend/lib/useProjectModel.ts` → 언어까지 함께 반환
- Modify: `frontend/app/page.tsx`, `admin/users/page.tsx`, `admin/models/page.tsx`, `projects/[projectId]/{dashboard,workspace,review,prototypes}/page.tsx` (배지 prop 전달)
- Test: `frontend/components/AppHeader.test.tsx`, `frontend/lib/useProjectModel.test.tsx`

**Interfaces:**
- Consumes: `useT()` (Task 1), `<LanguageSwitcher />` (Task 2), `GET /projects/{pid}` 응답의 `language` (Task 3)
- Produces:
  - `AppHeader` props: `{ activeTab, projectId?, modelLabel?, projectLanguage? }` — `projectLanguage?: "ko" | "en" | null`
  - `useProjectMeta(projectId): { modelLabel: string | null; language: Locale | null }` — `useProjectModel`을 대체한다

`useProjectModel`을 확장하지 않고 이름을 바꾸는 이유: 반환이 문자열에서 객체가 되므로 호출부 7곳이 전부 바뀐다. 같은 이름으로 두면 시그니처만 조용히 달라져, 병합 시 옛 호출부가 `label`을 문자열로 쓰다 깨진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `useProjectMeta`**

`frontend/lib/useProjectModel.test.tsx`를 열어 기존 테스트의 `useProjectModel` 임포트와 호출을 `useProjectMeta`로 바꾸고 단정을 `.modelLabel`로 고친다. 그런 다음 아래 테스트를 추가한다:

```typescript
it("프로젝트의 생성물 언어를 함께 돌려준다", async () => {
  server.use(
    http.get(`${API_BASE_URL}/projects/pilot1`, () =>
      HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                          model_id: null, language: "en" })),
    http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
  );
  const { result } = renderHook(() => useProjectMeta("pilot1"));
  await waitFor(() => expect(result.current.language).toBe("en"));
});

it("언어를 모르는 응답(구 백엔드)에서는 null이다 — 배지를 그리지 않는다", async () => {
  server.use(
    http.get(`${API_BASE_URL}/projects/pilot1`, () =>
      HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                          model_id: null })),
    http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
  );
  const { result } = renderHook(() => useProjectMeta("pilot1"));
  // 모델 조회가 끝나기를 기다린 뒤 언어가 여전히 null인지 본다.
  await waitFor(() => expect(result.current.modelLabel).toBeNull());
  expect(result.current.language).toBeNull();
});

it("projectId가 없으면 둘 다 null이다", () => {
  const { result } = renderHook(() => useProjectMeta(undefined));
  expect(result.current).toEqual({ modelLabel: null, language: null });
});
```

기존 파일에 `renderHook`/`waitFor`/`http`/`HttpResponse`/`server`/`API_BASE_URL` 임포트가 이미 있으면 그대로 쓴다. 없으면 파일 상단에 추가한다:

```typescript
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { useProjectMeta } from "./useProjectModel";
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/useProjectModel.test.tsx`
Expected: FAIL — `useProjectMeta is not a function` / `does not provide an export named 'useProjectMeta'`

- [ ] **Step 3: `types.ts`에 `language`를 추가한다**

`frontend/lib/api/types.ts`의 `ProjectSummary`와 `ProjectDetail` 인터페이스에 각각 추가한다(정확한 위치는 `model_id` 필드 바로 뒤):

```typescript
  // 이 프로젝트의 생성물 언어. UI 언어(pf_lang 쿠키)와 별개다 — 문서·
  // 프로토타입·채팅이 어느 언어로 나오는지. 백엔드는 항상 채워 보내지만
  // (미지정은 "ko"로 확정) 구 백엔드 응답에는 없을 수 있어 옵셔널이다.
  language?: "ko" | "en";
```

- [ ] **Step 4: `useProjectModel.ts`를 `useProjectMeta`로 고친다**

파일 전문을 교체:

```typescript
// frontend/lib/useProjectModel.ts
//
// 헤더 배지가 보여줄 두 값: 이 프로젝트가 도는 모델의 표시 이름과, 생성물
// 언어. 프로젝트마다 다르므로 화면에 없으면 지금 무엇으로 도는지 알 수 없다.
//
// 모델을 두 번 부르는 이유: 프로젝트는 model_id만 알고(매니페스트에 복사된 값),
// 사람이 읽는 이름은 카탈로그에만 있다. 대조 실패는 정상 경로다 — 관리자가
// 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있고, 그때는 id 원문을
// 보여준다(값을 복사해 두는 설계의 결과가 화면에서도 정직해야 한다).
//
// 언어는 그런 대조가 필요 없다 — 값 자체가 표시할 정보다.
"use client";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/api/client";
import { listModels } from "@/lib/api/models";
import { isLocale, type Locale } from "@/lib/i18n";

export interface ProjectMeta {
  /** 모델 표시 이름. null = 미지정(서버 env 기본값) 또는 조회 실패. */
  modelLabel: string | null;
  /** 생성물 언어. null = 구 백엔드 응답(필드 없음) 또는 조회 실패. */
  language: Locale | null;
}

const EMPTY: ProjectMeta = { modelLabel: null, language: null };

export function useProjectMeta(projectId: string | undefined): ProjectMeta {
  const [meta, setMeta] = useState<ProjectMeta>(EMPTY);

  useEffect(() => {
    if (!projectId) {
      setMeta(EMPTY);
      return;
    }
    let alive = true;
    // 실패는 배지가 빠지는 것으로 끝난다 — 화면의 다른 것을 막지 않는다.
    void Promise.all([
      getProject(projectId),
      listModels().catch(() => []),
    ])
      .then(([project, models]) => {
        if (!alive) return;
        const id = project.model_id;
        setMeta({
          // 미지정: 서버가 env 기본값으로 도는데 그 값을 프론트는 알 수 없다.
          modelLabel: id ? models.find((m) => m.model_id === id)?.name ?? id : null,
          // isLocale로 좁힌다 — 구 백엔드는 이 필드가 없고, 손상된 응답이
          // 임의 문자열을 실어 올 수도 있다. 그때는 배지를 그리지 않는다.
          language: isLocale(project.language) ? project.language : null,
        });
      })
      .catch(() => {
        if (alive) setMeta(EMPTY);
      });
    return () => { alive = false; };
  }, [projectId]);

  return meta;
}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/useProjectModel.test.tsx`
Expected: PASS

- [ ] **Step 6: 실패하는 테스트를 쓴다 — 헤더**

`frontend/components/AppHeader.test.tsx` 끝에 추가:

```typescript
import { LocaleProvider } from "@/lib/i18n/provider";

describe("AppHeader 로케일", () => {
  it("영어 로케일에서 탭 라벨이 영어다", () => {
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="dashboard" projectId="pilot1" />
      </LocaleProvider>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href", "/projects/pilot1/dashboard",
    );
    expect(screen.getByRole("link", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Document Review" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Prototypes" })).toBeInTheDocument();
  });

  it("Provider 없이 렌더하면 한국어다 — 기존 테스트가 그대로 통과하는 근거", () => {
    render(<AppHeader activeTab="dashboard" projectId="pilot1" />);
    expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  });

  it("언어 스위치를 그린다", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.getByRole("group", { name: "Language / 언어" })).toBeInTheDocument();
  });
});

describe("AppHeader 언어 배지", () => {
  it("프로젝트 언어를 배지로 보여준다", () => {
    render(<AppHeader activeTab="dashboard" projectId="pilot1" projectLanguage="en" />);
    const badge = screen.getByTestId("language-badge");
    expect(badge).toHaveTextContent("English");
  });

  it("UI 언어와 프로젝트 언어가 달라도 프로젝트 언어를 보여준다", () => {
    // 이 배지의 목적이 바로 이 상황을 드러내는 것이다 — 영어 UI로 한국어
    // 프로젝트를 열면 문서는 한국어로 나온다.
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="dashboard" projectId="pilot1" projectLanguage="ko" />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("language-badge")).toHaveTextContent("한국어");
  });

  it("언어를 모르면 배지를 그리지 않는다", () => {
    render(<AppHeader activeTab="dashboard" projectId="pilot1" />);
    expect(screen.queryByTestId("language-badge")).toBeNull();
  });
});
```

- [ ] **Step 7: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/AppHeader.test.tsx`
Expected: FAIL — 영어 라벨을 못 찾고, `language-badge`가 없다

- [ ] **Step 8: 딕셔너리에 헤더 키를 추가한다**

`frontend/lib/i18n/ko.ts`:

```typescript
export const ko = {
  "nav.dashboard": "대시보드",
  "nav.workspace": "워크스페이스",
  "nav.review": "문서 리뷰",
  "nav.prototypes": "프로토타입",
  "nav.ariaLabel": "주요 메뉴",
  "nav.needProject": "프로젝트를 먼저 선택하세요",
  "header.modelBadgeTitle": "이 프로젝트가 사용하는 AI 모델",
  "header.bedrockConnected": "Bedrock 연결됨",
  "header.languageBadgeTitle": "이 프로젝트의 문서·프로토타입·채팅 언어",
} as const;
```

`frontend/lib/i18n/en.ts`:

```typescript
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
};
```

- [ ] **Step 9: `AppHeader.tsx`를 고친다**

파일 전문을 교체:

```typescript
"use client";
import Link from "next/link";

import type { Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

import { LanguageSwitcher } from "./LanguageSwitcher";
import { UserMenu } from "./UserMenu";

export type HeaderTab = "dashboard" | "workspace" | "review" | "prototypes" | "projects";

// 언어 배지의 표기. 딕셔너리를 타지 않는다 — 언어 이름은 항상 그 언어 자체로
// 적는다(LanguageSwitcher의 라벨과 같은 규약). "한국어"를 영어 UI에서 "Korean"
// 으로 바꾸면 그 프로젝트의 문서가 실제로 어떤 글자로 나오는지 흐려진다.
const LANGUAGE_LABEL: Record<Locale, string> = { ko: "한국어", en: "English" };

// Ported from the shared <header> in files/ui/01–03. `projectId` is optional so
// the project-list screen (no project chosen yet) can render the header. When no
// project is selected the per-project tabs render DISABLED (non-clickable, not
// links) — they require a project, so a live link there would navigate to a
// dead `#/…` route and appear broken. Once a project is selected the tabs link
// into that project's routes.
export function AppHeader({
  activeTab,
  projectId,
  modelLabel,
  projectLanguage,
}: {
  activeTab: HeaderTab;
  projectId?: string;
  // 이 프로젝트가 도는 모델의 표시 이름. null/undefined면 배지를 그리지
  // 않는다 — 프로젝트가 없는 화면이거나, 모델 미지정(서버 env 기본값)이다.
  modelLabel?: string | null;
  // 이 프로젝트의 **생성물 언어**. UI 로케일과 다를 수 있고, 그것이 정상이다 —
  // 영어 UI로 한국어 프로젝트를 열면 문서는 한국어로 나온다. 이 배지가 그
  // 사실을 화면에 드러낸다. null/undefined면 그리지 않는다(프로젝트 없는
  // 화면, 또는 언어를 모르는 구 백엔드 응답).
  projectLanguage?: Locale | null;
}) {
  const t = useT();

  const tab = (key: HeaderTab, label: string, href: string) => {
    const active = key === activeTab;
    const base = "px-3 py-2 rounded-lg text-sm";
    // Per-project tab with no project selected: disabled, not a link.
    if (!projectId) {
      return (
        <span
          className={`${base} text-slate-300 cursor-not-allowed select-none`}
          aria-disabled="true"
          title={t("nav.needProject")}
        >
          {label}
        </span>
      );
    }
    const cls = active
      ? `${base} bg-violet-50 text-violet-700 font-medium`
      : `${base} hover:bg-slate-100 text-slate-600`;
    return (
      <Link href={href} className={cls} aria-current={active ? "page" : undefined}>
        {label}
      </Link>
    );
  };

  const base = `/projects/${projectId}`;
  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Link href="/" className="flex items-center gap-2 font-bold text-lg text-violet-700">
            <span className="w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-sm font-bold">
              AI
            </span>
            Pathfinder
          </Link>
          <nav className="hidden md:flex items-center gap-1" aria-label={t("nav.ariaLabel")}>
            {tab("dashboard", t("nav.dashboard"), `${base}/dashboard`)}
            {tab("workspace", t("nav.workspace"), `${base}/workspace`)}
            {tab("review", t("nav.review"), `${base}/review`)}
            {tab("prototypes", t("nav.prototypes"), `${base}/prototypes`)}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {projectLanguage && (
            <span
              data-testid="language-badge"
              title={t("header.languageBadgeTitle")}
              className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-slate-50 text-slate-600 border border-slate-200"
            >
              {LANGUAGE_LABEL[projectLanguage]}
            </span>
          )}
          {modelLabel && (
            <span
              data-testid="model-badge"
              title={t("header.modelBadgeTitle")}
              className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-violet-50 text-violet-700 border border-violet-200"
            >
              {modelLabel}
            </span>
          )}
          <span className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> {t("header.bedrockConnected")}
          </span>
          <LanguageSwitcher />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
```

- [ ] **Step 10: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/AppHeader.test.tsx`
Expected: PASS. 기존 테스트("no longer shows the retired 질문 답변 / 빌드 캔버스 tabs" 등)도 함께 통과해야 한다 — Provider 없이 렌더하므로 `ko` 폴백이 걸린다.

- [ ] **Step 11: 호출부 7곳을 고친다**

`useProjectModel`을 쓰는 4개 페이지(`dashboard`, `workspace`, `review`, `prototypes`)에서 임포트와 사용을 바꾼다. 각 파일에서:

```typescript
// 이전
import { useProjectModel } from "@/lib/useProjectModel";
const modelLabel = useProjectModel(projectId);
// ...
<AppHeader activeTab="dashboard" projectId={projectId} modelLabel={modelLabel} />

// 이후
import { useProjectMeta } from "@/lib/useProjectModel";
const { modelLabel, language } = useProjectMeta(projectId);
// ...
<AppHeader activeTab="dashboard" projectId={projectId} modelLabel={modelLabel}
           projectLanguage={language} />
```

`activeTab` 값은 각 페이지의 기존 값을 유지한다(`dashboard`/`workspace`/`review`/`prototypes`).

프로젝트가 없는 3개 페이지(`app/page.tsx`, `admin/users/page.tsx`, `admin/models/page.tsx`)는 **고치지 않는다** — `<AppHeader activeTab="projects" />`가 그대로 맞다(배지 둘 다 없음).

- [ ] **Step 12: 타입 검사와 빌드**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -10`
Expected: 에러 없음. `useProjectModel`을 옛 시그니처로 부르는 곳이 남아 있으면 여기서 잡힌다.

- [ ] **Step 13: 전체 스위트로 회귀가 없음을 확인한다**

Run: `cd frontend && npx vitest run 2>&1 | tail -5`
Expected: 모든 파일 통과. 기존 664가 줄지 않았는지 확인한다(신규 단정이 더해져 총계는 늘어난다).

- [ ] **Step 14: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/components/AppHeader.tsx frontend/components/AppHeader.test.tsx \
        frontend/lib/i18n/ko.ts frontend/lib/i18n/en.ts \
        frontend/lib/api/types.ts frontend/lib/useProjectModel.ts \
        frontend/lib/useProjectModel.test.tsx \
        'frontend/app/projects/[projectId]/dashboard/page.tsx' \
        'frontend/app/projects/[projectId]/workspace/page.tsx' \
        'frontend/app/projects/[projectId]/review/page.tsx' \
        'frontend/app/projects/[projectId]/prototypes/page.tsx'
git commit -m "feat(i18n): 헤더 번역 + 언어 스위치 + 프로젝트 언어 배지

useProjectModel을 useProjectMeta로 개명했다 — 반환이 문자열에서 객체가 되므로
같은 이름으로 두면 옛 호출부가 label을 문자열로 쓰다 조용히 깨진다.

언어 배지는 UI 로케일이 아니라 프로젝트의 생성물 언어를 보여준다. 둘이 다른
것이 정상이고, 이 배지가 그 사실을 드러내는 자리다."
```

---

## Task 5: 승인 게이트 — 프로젝트 언어의 승인 단어

**Files:**
- Create: `frontend/lib/approvalMarker.ts`
- Create: `frontend/lib/approvalMarker.test.ts`
- Modify: `frontend/lib/approvalState.ts:17`
- Modify: `frontend/app/projects/[projectId]/review/page.tsx:128`
- Test: `frontend/lib/approvalState.test.ts`, `frontend/app/projects/[projectId]/review/page.test.tsx`

**Interfaces:**
- Consumes: `Locale` (Task 1), `useProjectMeta` (Task 4)
- Produces:
  - `function approvalTurnText(language: Locale): string` — `"승인"` | `"Approved"`
  - `const APPROVAL_RE: RegExp` — 두 언어를 다 받는 판정식
  - `function isApprovalText(text: string): boolean`

**이것이 이 스펙의 유일한 진짜 결함이다.** 현재 `review/page.tsx:128`이 `sendTurn("승인")`을 보내고 `approvalState.ts:17`이 `/^\s*승인\s*$/`로 판정한다. 영어 프로젝트에서 영어 라벨을 누르면 게이트가 영원히 열리지 않는다.

**불투명 마커(`[APPROVED]`)를 쓰지 않는다.** 이 텍스트는 `postMessage`로 **에이전트에게 가고**(`review/page.tsx:89-93`) 트랜스크립트와 채팅 히스토리에 사용자 말풍선으로 남는다. 에이전트가 승인으로 이해해야 하고 사람이 읽어야 한다.

**UI 언어가 아니라 프로젝트 언어를 쓴다.** 영어 UI로 한국어 프로젝트를 승인하면 대화는 한국어로 진행되고 있으므로 `승인`이 가야 한다. 버튼 라벨만 UI 언어로 번역된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/approvalMarker.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { approvalTurnText, isApprovalText } from "./approvalMarker";

describe("approvalTurnText", () => {
  it("프로젝트 언어의 승인 단어를 준다", () => {
    expect(approvalTurnText("ko")).toBe("승인");
    expect(approvalTurnText("en")).toBe("Approved");
  });
});

describe("isApprovalText", () => {
  it("두 언어를 다 인식한다", () => {
    expect(isApprovalText("승인")).toBe(true);
    expect(isApprovalText("Approved")).toBe(true);
  });

  it("영어는 대소문자를 가리지 않는다 — 에이전트가 감사 로그에 옮겨 적을 때 표기가 흔들린다", () => {
    expect(isApprovalText("approved")).toBe(true);
    expect(isApprovalText("APPROVED")).toBe(true);
  });

  it("앞뒤 공백을 허용한다", () => {
    expect(isApprovalText("  승인  ")).toBe(true);
    expect(isApprovalText("\nApproved\n")).toBe(true);
  });

  it("문장 속에 든 승인은 인식하지 않는다", () => {
    // 게이트가 보낸 턴만 결정으로 센다 — AI가 승인을 언급하는 문장이
    // 결정으로 세어지면 PM이 누르기 전에 게이트가 사라진다.
    expect(isApprovalText("승인 게이트에서 승인하시면 됩니다")).toBe(false);
    expect(isApprovalText("I have approved the document")).toBe(false);
  });

  it("빈 문자열은 아니다", () => {
    expect(isApprovalText("")).toBe(false);
    expect(isApprovalText("   ")).toBe(false);
  });

  it("두 함수가 어긋나지 않는다 — 보낼 단어는 반드시 판정을 통과한다", () => {
    // 한쪽만 바뀌면 게이트가 조용히 안 열린다. 이 단정이 그 회귀를 막는다.
    for (const lang of ["ko", "en"] as const) {
      expect(isApprovalText(approvalTurnText(lang))).toBe(true);
    }
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/approvalMarker.test.ts`
Expected: FAIL — `Failed to resolve import "./approvalMarker"`

- [ ] **Step 3: `approvalMarker.ts`를 만든다**

```typescript
// frontend/lib/approvalMarker.ts — 승인 턴 텍스트와 판정식의 단일 출처.
//
// 이 두 가지가 어긋나면 승인 게이트가 조용히 열리지 않는다: 프론트가 보낸
// 텍스트를 감사 로그에서 되찾지 못하고, 사용자는 버튼을 눌렀는데 아무 일도
// 일어나지 않은 것으로 본다. 그래서 한 파일에 둔다.
//
// 왜 불투명 마커(`[APPROVED]`)가 아닌가: 이 텍스트는 기계 신호가 아니다.
// review/page.tsx의 sendTurn이 postMessage로 이것을 에이전트에게 보내고, 그
// 턴은 트랜스크립트와 채팅 히스토리에 사용자 말풍선으로 남는다. 에이전트가
// 승인으로 이해해야 하고 사람이 읽어야 한다. 프로젝트 언어의 승인 단어는
// 에이전트가 이미 그 언어로 대화하고 있으므로 추가 프롬프트 지원 없이 통한다.
//
// 상류 AI-PLC 룰은 "user explicitly approves"만 요구하고 키워드를 정의하지
// 않는다(envision.md:412, product-strategy.md:154, go-to-market.md:160) —
// 즉 이 단어는 우리가 정한 프로토콜이고, 그래서 우리가 두 언어를 다 다뤄야 한다.
import type { Locale } from "@/lib/i18n";

const TURN_TEXT: Record<Locale, string> = { ko: "승인", en: "Approved" };

/**
 * 승인 턴으로 보낼 텍스트. **UI 로케일이 아니라 프로젝트 언어**를 받는다.
 *
 * 영어 UI로 한국어 프로젝트를 승인하면 대화는 한국어로 진행되고 있으므로
 * "승인"이 가야 한다. 버튼 라벨만 UI 언어로 번역된다.
 */
export function approvalTurnText(language: Locale): string {
  return TURN_TEXT[language];
}

// 두 언어를 다 받는다. 기존 한국어 감사 로그가 계속 인식되어야 하고,
// parsers/audit.py가 `사용자 입력|User Raw Input`을 둘 다 받는 것과 같은 규율이다.
//
// 영어에 `i` 플래그를 주는 이유: 감사 로그는 에이전트가 사용자 입력을 옮겨
// 적은 것이라 표기가 흔들린다("approved", "Approved"). 한국어에는 대소문자가
// 없어 영향이 없다.
//
// `^...$`로 묶어 전체 일치를 요구한다 — 문장 속의 언급을 결정으로 세면 PM이
// 누르기 전에 게이트가 사라진다(approvalState.test.ts의 그 테스트).
const APPROVAL_RE = /^\s*(승인|Approved)\s*$/i;

/** 이 감사 로그 항목의 user_input이 승인 결정인가. */
export function isApprovalText(text: string): boolean {
  return APPROVAL_RE.test(text);
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/approvalMarker.test.ts`
Expected: PASS (7 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — `approvalState`가 영어를 인식한다**

`frontend/lib/approvalState.test.ts`의 `describe("deriveApprovalState")` 안에 추가:

```typescript
  it("영어 프로젝트의 Approved 턴도 승인으로 센다", () => {
    const state = deriveApprovalState([
      entry(1, "I want to improve the NOTAM dashboard", "Discovery started"),
      entry(2, "Approved", "Approval recorded — Discovery is complete.", "Final approval"),
    ]);
    expect(state).toEqual({ approved: true, approvedAtIndex: 2 });
  });

  it("영어 표기가 흔들려도 인식한다", () => {
    // 감사 로그는 에이전트가 옮겨 적은 것이라 대소문자가 일정하지 않다.
    const state = deriveApprovalState([entry(1, "approved", "ok", "Final approval")]);
    expect(state.approved).toBe(true);
  });

  it("문장 속의 approved는 결정이 아니다", () => {
    const state = deriveApprovalState([
      entry(1, "what's next?", "Once you have approved, we move to Inception."),
    ]);
    expect(state.approved).toBe(false);
  });
```

- [ ] **Step 6: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/approvalState.test.ts`
Expected: FAIL — 영어 두 테스트가 `approved: false`

- [ ] **Step 7: `approvalState.ts`가 공유 판정식을 쓰게 한다**

`frontend/lib/approvalState.ts`의 상단 임포트에 추가:

```typescript
import { isApprovalText } from "@/lib/approvalMarker";
```

`isApproval` 함수(14-18행)를 교체:

```typescript
/** An audit entry that records an approval decision at the gate. */
function isApproval(e: AuditEntry): boolean {
  // 판정식은 approvalMarker.ts가 소유한다 — 게이트가 보내는 텍스트와 같은
  // 파일에 두어야 한쪽만 바뀌는 일이 없다. 두 언어를 다 받으므로 기존 한국어
  // 감사 로그도 계속 인식된다.
  //
  // INPUT을 보고 AI의 산문을 보지 않는 이유는 그대로다: 다른 답변에 등장한
  // "승인"이 결정으로 세어지면 PM이 누르기 전에 게이트가 사라진다.
  return isApprovalText(e.user_input ?? "");
}
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/approvalState.test.ts`
Expected: PASS — 기존 테스트(`승인` 인식, 문장 속 언급 무시)도 함께 통과한다

- [ ] **Step 9: 실패하는 테스트를 쓴다 — 리뷰 페이지가 프로젝트 언어로 보낸다**

`frontend/app/projects/[projectId]/review/page.test.tsx`에 추가한다. 기존 `clicking Approve POSTs {text:'승인'}` 테스트가 있는 `describe` 안에:

```typescript
  it("영어 프로젝트에서는 {text:'Approved'}를 보낸다", async () => {
    mockTreeAndAudit();
    // 프로젝트 언어가 en임을 알려주는 응답. useProjectMeta가 이것을 읽는다.
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1`, () =>
        HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                            model_id: null, language: "en" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    let body: unknown = null;
    server.use(
      http.post(`${API_BASE_URL}/projects/pilot1/message`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    render(<ReviewPage params={params} />);
    const button = await screen.findByRole("button", { name: /승인하고 다음 단계로/ });
    await userEvent.click(button);
    // 대화가 영어로 진행되고 있으므로 영어 승인 단어가 가야 한다. 버튼 라벨은
    // UI 로케일(여기서는 기본값 ko)이므로 한국어인 것이 맞다 — 이 둘이 다른
    // 것이 정상이다.
    await waitFor(() => expect(body).toEqual({ text: "Approved" }));
  });
```

- [ ] **Step 10: 실패를 확인한다**

Run: `cd frontend && npx vitest run 'app/projects/[projectId]/review/page.test.tsx'`
Expected: FAIL — `body`가 `{ text: "승인" }`

- [ ] **Step 11: `review/page.tsx`를 고친다**

`useProjectMeta` 호출은 Task 4에서 이미 `language`를 꺼내도록 바뀌어 있다. 임포트에 추가:

```typescript
import { approvalTurnText } from "@/lib/approvalMarker";
import { DEFAULT_LOCALE } from "@/lib/i18n";
```

128행의 `onApprove`를 교체:

```typescript
              // 프로젝트 언어의 승인 단어를 보낸다 — 이 텍스트는 에이전트에게
              // 가고 트랜스크립트에 남으므로, 대화가 진행되는 언어여야 한다.
              // language가 null인 경우(구 백엔드, 조회 실패)는 ko로 떨어진다 —
              // 그것이 이 기능 이전 모든 프로젝트의 언어다.
              onApprove={() => sendTurn(approvalTurnText(language ?? DEFAULT_LOCALE))}
```

- [ ] **Step 12: 통과를 확인한다**

Run: `cd frontend && npx vitest run 'app/projects/[projectId]/review/page.test.tsx'`
Expected: PASS — 기존 `{text:'승인'}` 테스트도 통과한다(그 테스트의 `/projects/pilot1` 목은 `language`를 안 실어 보내므로 `null` → `ko` 폴백)

- [ ] **Step 13: 타입 검사 + 전체 스위트**

Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -5`
Expected: 타입 에러 없음, 모든 테스트 통과

- [ ] **Step 14: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/approvalMarker.ts frontend/lib/approvalMarker.test.ts \
        frontend/lib/approvalState.ts frontend/lib/approvalState.test.ts \
        'frontend/app/projects/[projectId]/review/page.tsx' \
        'frontend/app/projects/[projectId]/review/page.test.tsx'
git commit -m "fix(review): 승인 게이트가 영어 프로젝트에서 열리지 않던 결함

턴 텍스트로 '승인'을 보내고 같은 문자열을 정규식으로 판정하고 있었다 — 영어
프로젝트에서는 게이트가 영원히 열리지 않는다.

프로젝트 언어(UI 언어가 아니다)의 승인 단어를 보내고, 판정은 두 언어를 다
받는다. 불투명 마커를 쓰지 않은 이유는 이 텍스트가 에이전트에게 가고
트랜스크립트에 사용자 말풍선으로 남기 때문이다."
```

---

## Task 6: `session_history` — 답변 접두사와 요약 문구

**Files:**
- Modify: `backend/pathfinder/agent/strands_tools.py:85`
- Modify: `backend/pathfinder/session_history.py:105-122, 137-158`
- Modify: `backend/pathfinder/models.py` (`HistoryItem.answers`)
- Modify: `frontend/lib/api/types.ts` (`HistoryItem.answers`)
- Modify: `frontend/components/canvas/ChatTimeline.tsx:94`
- Modify: `frontend/lib/i18n/ko.ts`, `en.ts`
- Test: `backend/tests/test_session_history.py`, `frontend/components/canvas/ChatTimeline.test.tsx`

**Interfaces:**
- Consumes: `useT()` (Task 1)
- Produces:
  - 백엔드: `HistoryItem.answers: dict[str, str] | None` — 파싱된 답변 dict, 아니면 `None`
  - 프론트: `HistoryItem.answers?: Record<string, string> | null`
  - 딕셔너리 키: `chat.answersSubmitted` (`"답변 제출"` / `"Answers submitted"`)

**두 가지를 함께 고친다.**

(a) `strands_tools.py:85`가 `f"사용자 답변: {json}"`을 만들고 `session_history.py:109·149`가 그 접두사를 제거한다. 생산자와 소비자가 같은 리포 안에 있으므로 접두사를 언어 중립으로 바꾸고 양쪽을 고친다. **기존 트랜스크립트 호환을 위해 제거는 두 형태를 모두 시도한다.**

(b) `"답변 제출 — 1: A · 2: B"` 요약 문구를 백엔드가 만드는데 백엔드는 UI 언어를 모른다. `answers` dict를 그대로 넘기고 프론트가 UI 언어로 렌더한다. **`text`는 그대로 채워 보낸다** — `answers`를 모르는 구 프론트가 빈 말풍선을 띄우지 않게 하는 폴백이다.

`answerSummary`(라이브 경로)를 재사용하지 않는다: 그 함수는 선택지 문자를 옵션 텍스트로 펼치기 위해 `QuestionFile`을 요구하고, 복원 경로에는 그것이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 백엔드**

`backend/tests/test_session_history.py` 끝에 추가:

```python
# ---- 언어 중립 접두사 + answers 전달 ----

def test_parses_the_language_neutral_prefix():
    # strands_tools가 이제 "[answers] {...}"를 만든다.
    items = transform_messages([
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '[answers] {"1": "A"}'}]}}]},
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.answers == {"1": "A"}


def test_still_parses_the_legacy_korean_prefix():
    # 이미 S3에 있는 트랜스크립트는 옛 접두사를 쓴다 — 이것이 깨지면 진행 중인
    # 워크숍의 채팅 히스토리가 전부 빈 말풍선이 된다.
    items = transform_messages([
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '사용자 답변: {"1": "A"}'}]}}]},
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.answers == {"1": "A"}


def test_free_text_answer_has_no_answers_dict():
    # JSON이 아닌 자유 서술은 dict로 펼 수 없다. text 폴백만 남는다.
    items = transform_messages([
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": "[answers] 자유 서술 응답"}]}}]},
    ])
    answer = next(i for i in items if i.role == "user" and i.text)
    assert answer.answers is None
    assert "자유 서술 응답" in answer.text


def test_text_is_still_filled_as_a_fallback():
    # answers를 모르는 구 프론트가 빈 말풍선을 띄우지 않게 한다.
    items = transform_messages([
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '[answers] {"1": "A"}'}]}}]},
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.text and answer.text.strip() != ""
```

기존 테스트 중 `answers[0].text == "답변 제출 — 1: A"`처럼 **문구를 단정하는 것들**(38-39, 121-122, 149, 163, 406, 442행)은 그대로 둔다 — `text` 폴백을 계속 채우므로 통과한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_session_history.py -q 2>&1 | tail -8`
Expected: FAIL — `[answers]` 접두사 테스트들이 실패하고, `HistoryItem`에 `answers` 속성이 없다는 에러

- [ ] **Step 3: `models.py`에 `answers`를 추가한다**

`backend/pathfinder/models.py`의 `HistoryItem`에 필드를 추가:

```python
class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
    # role=="ai"일 때 그 턴의 도구 실행 트레이스(없으면 빈 리스트)
    trace: list[HistoryTraceEntry] = []
    # 답변 제출 턴의 구조화된 답변({"1": "A", "2": "B,C"}). 사람이 읽는 문구는
    # 프론트가 UI 언어로 만든다 — 백엔드는 UI 언어를 모른다. JSON이 아닌 자유
    # 서술 답변은 dict로 펼 수 없어 None이고, 그때는 text만 쓴다.
    answers: dict[str, str] | None = None
```

- [ ] **Step 4: `strands_tools.py`의 접두사를 바꾼다**

85행을 교체:

```python
        # 언어 중립 접두사. session_history가 이것을 벗겨 answers dict를
        # 복원한다 — 그쪽은 구 트랜스크립트의 "사용자 답변: "도 함께 받는다.
        return f"{ANSWER_PREFIX}{json.dumps(answers, ensure_ascii=False)}"
```

같은 파일 상단(임포트 뒤)에 상수를 추가:

```python
#: ask_questions tool_result의 접두사. session_history._strip_answer_prefix가
#: 같은 값을 벗긴다 — 두 곳이 어긋나면 채팅 히스토리의 답변 말풍선이 깨진다.
#: 언어 중립인 이유: 이 문자열은 사용자에게 보이지 않고(프론트가 UI 언어로
#: 문구를 만든다) 파싱 계약일 뿐이다.
ANSWER_PREFIX = "[answers] "
```

- [ ] **Step 5: `session_history.py`를 고친다**

파일 상단에 헬퍼를 추가(임포트 뒤):

```python
#: 구 트랜스크립트의 접두사. 이미 S3에 있는 대화가 이것을 쓰므로 영구히
#: 받아 준다 — 지우면 진행 중인 워크숍의 히스토리가 빈 말풍선이 된다.
#: parsers/audit.py가 `사용자 입력|User Raw Input`을 둘 다 받는 것과 같은 규율.
_LEGACY_ANSWER_PREFIX = "사용자 답변: "


def _strip_answer_prefix(raw: str) -> str:
    """ask_questions tool_result 본문에서 접두사를 벗긴다. 신·구 두 형태."""
    from pathfinder.agent.strands_tools import ANSWER_PREFIX
    for prefix in (ANSWER_PREFIX, _LEGACY_ANSWER_PREFIX):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def _parse_answers(raw: str) -> dict[str, str] | None:
    """접두사를 벗긴 본문 → 답변 dict, 펼 수 없으면 None.

    None은 자유 서술 답변(JSON이 아닌 것)이다. 그때는 호출부가 text 폴백만
    채운다 — 프론트가 dict 없이도 말풍선을 그릴 수 있어야 한다.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    # 값이 문자열이 아닌 경우(에이전트가 숫자를 넣는 등)도 문자열로 통일한다 —
    # 프론트의 Record<string, string> 계약을 지킨다.
    return {str(k): str(v) for k, v in parsed.items()}
```

`transform_messages`의 toolResult 분기(105-122행)를 교체:

```python
                if tr.get("toolUseId") in ask_ids:
                    inner = "".join(c.get("text", "") for c in tr.get("content", []))
                    body = _strip_answer_prefix(inner)
                    answers = _parse_answers(body)
                    # text는 폴백으로 계속 채운다 — answers를 모르는 구
                    # 프론트가 빈 말풍선을 띄우지 않게 한다. 사람이 읽는 문구는
                    # answers가 있으면 프론트가 UI 언어로 다시 만든다.
                    items.append(HistoryItem(
                        role="user",
                        text=redact_credentials(_answer_fallback_text(body, answers)),
                        answers=answers))
```

`_cli_answer_summary`(137-158행)를 교체하고 폴백 문구 생성기를 함께 둔다:

```python
def _answer_fallback_text(body: str, answers: dict[str, str] | None) -> str:
    """answers를 모르는 소비자를 위한 한국어 폴백 문구.

    사람이 읽는 최종 문구는 프론트가 UI 언어로 만든다(HistoryItem.answers).
    여기 남은 한국어는 그 필드를 모르는 구 프론트가 빈 말풍선을 띄우지 않게
    하는 안전망일 뿐이다 — 새 프론트는 이 값을 무시한다.
    """
    if answers:
        pretty = " · ".join(f"{k}: {v}" for k, v in
                            sorted(answers.items(), key=lambda kv: str(kv[0])))
        return f"답변 제출 — {pretty}"
    return f"답변 제출: {body}"


def _cli_answer_summary(content: object) -> tuple[str, dict[str, str] | None]:
    """ask_questions tool_result 본문 → (폴백 문구, answers dict 또는 None).

    Converse 경로(transform_messages)와 같은 규칙을 쓴다: 접두사를 벗기고
    (신·구 두 형태) JSON이면 dict로 편다. 반환이 tuple로 바뀐 것에 주의 —
    호출부가 HistoryItem의 text와 answers를 함께 채운다.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    body = _strip_answer_prefix(inner)
    answers = _parse_answers(body)
    return _answer_fallback_text(body, answers), answers
```

`_cli_answer_summary`의 호출부를 찾아 tuple 언패킹으로 고친다:

Run: `cd backend && grep -n "_cli_answer_summary" pathfinder/session_history.py`

각 호출부를 아래 모양으로 바꾼다:

```python
            text, answers = _cli_answer_summary(tr.get("content"))
            items.append(HistoryItem(role="user",
                                     text=redact_credentials(text),
                                     answers=answers))
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_session_history.py -q`
Expected: PASS — 신규 4개와 기존 전부

- [ ] **Step 7: 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 전부 통과. `test_agent_driver.py`가 `사용자 답변: `을 단정하면 `[answers] `로 함께 고친다.

- [ ] **Step 8: 실패하는 테스트를 쓴다 — 프론트가 UI 언어로 렌더**

`frontend/components/canvas/ChatTimeline.test.tsx`에 추가:

```typescript
import { LocaleProvider } from "@/lib/i18n/provider";

describe("답변 제출 말풍선", () => {
  // ChatTimeline의 items prop 모양은 이 파일의 기존 테스트를 따른다.
  const answerItem = {
    id: "a1",
    role: "user" as const,
    text: "답변 제출 — 1: A · 2: B",
    card: null,
    name: null,
    trace: [],
    answers: { "1": "A", "2": "B" },
  };

  it("answers가 있으면 UI 언어로 문구를 만든다", () => {
    render(
      <LocaleProvider locale="en">
        <ChatTimeline items={[answerItem]} />
      </LocaleProvider>,
    );
    // 백엔드가 실어 보낸 한국어 text는 무시하고 UI 언어로 다시 만든다.
    expect(screen.getByText(/Answers submitted/)).toBeInTheDocument();
    expect(screen.getByText(/1: A · 2: B/)).toBeInTheDocument();
    expect(screen.queryByText(/답변 제출/)).toBeNull();
  });

  it("한국어 UI에서는 한국어 문구다", () => {
    render(
      <LocaleProvider locale="ko">
        <ChatTimeline items={[answerItem]} />
      </LocaleProvider>,
    );
    expect(screen.getByText(/답변 제출/)).toBeInTheDocument();
  });

  it("answers가 없으면 백엔드의 text를 그대로 쓴다", () => {
    // 자유 서술 답변, 또는 answers를 안 실어 보내는 구 백엔드.
    render(
      <LocaleProvider locale="en">
        <ChatTimeline items={[{ ...answerItem, answers: null,
                                text: "답변 제출: 자유 서술" }]} />
      </LocaleProvider>,
    );
    expect(screen.getByText("답변 제출: 자유 서술")).toBeInTheDocument();
  });
});
```

기존 테스트가 `items`를 다른 모양으로 넘기면 그 모양을 따른다 — `ChatTimeline`의 실제 prop 타입을 확인한다: `cd frontend && grep -n "export function ChatTimeline" -A 12 components/canvas/ChatTimeline.tsx`

- [ ] **Step 9: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx`
Expected: FAIL — `Answers submitted`를 못 찾는다

- [ ] **Step 10: 딕셔너리와 타입, 렌더를 고친다**

`frontend/lib/i18n/ko.ts`에 추가: `"chat.answersSubmitted": "답변 제출",`
`frontend/lib/i18n/en.ts`에 추가: `"chat.answersSubmitted": "Answers submitted",`

`frontend/lib/api/types.ts`의 `HistoryItem`에 추가:

```typescript
  // 답변 제출 턴의 구조화된 답변. 있으면 프론트가 UI 언어로 문구를 만들고,
  // 없으면(자유 서술 답변, 또는 이 필드를 모르는 구 백엔드) text를 그대로 쓴다.
  answers?: Record<string, string> | null;
```

`frontend/components/canvas/ChatTimeline.tsx`의 94행을 교체한다. 컴포넌트 본문 상단에서 `const t = useT();`를 얻고(없으면 추가), 임포트에 `import { useT } from "@/lib/i18n/provider";`를 넣는다:

```typescript
            if (item.role === "user") {
              // answers가 있으면 UI 언어로 문구를 다시 만든다 — 백엔드의 text는
              // 이 필드를 모르는 구 프론트를 위한 한국어 폴백일 뿐이다.
              const text = item.answers
                ? `${t("chat.answersSubmitted")} — ${Object.entries(item.answers)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(" · ")}`
                : item.text;
              return <UserMessage key={item.id} text={text} />;
            }
```

정렬을 `localeCompare`로 하는 이유: 백엔드가 `sorted(key=str)`로 정렬해 보내므로 같은 순서를 유지한다.

- [ ] **Step 11: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx`
Expected: PASS

- [ ] **Step 12: 타입 검사 + 양쪽 전체 스위트**

Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -4`
Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 양쪽 전부 통과

- [ ] **Step 13: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/agent/strands_tools.py backend/pathfinder/session_history.py \
        backend/pathfinder/models.py backend/tests/test_session_history.py \
        frontend/lib/api/types.ts frontend/components/canvas/ChatTimeline.tsx \
        frontend/components/canvas/ChatTimeline.test.tsx \
        frontend/lib/i18n/ko.ts frontend/lib/i18n/en.ts
git commit -m "refactor(history): 답변 요약 문구를 프론트로, 접두사를 언어 중립으로

백엔드는 UI 언어를 모르므로 answers dict만 넘기고 문구는 프론트가 만든다.
text는 폴백으로 계속 채운다 — 이 필드를 모르는 구 프론트가 빈 말풍선을
띄우지 않게 한다.

접두사는 '[answers] '로 바꾸되 구 트랜스크립트의 '사용자 답변: '도 영구히
받는다. 지우면 진행 중인 워크숍의 히스토리가 전부 빈 말풍선이 된다."
```

---

## Task 7: 백엔드 에러 문구 → 코드

**Files:**
- Create: `backend/pathfinder/error_codes.py`
- Create: `frontend/lib/api/errorMessage.ts`
- Create: `frontend/lib/api/errorMessage.test.ts`
- Modify: `backend/pathfinder/routes/admin_users.py` (44-51, 62, 100, 114, 159행)
- Modify: `backend/pathfinder/routes/models.py` (102, 104, 107, 121행)
- Modify: `backend/pathfinder/routes/prototypes.py` (258, 430, 521행)
- Modify: `backend/pathfinder/routes/surveys_public.py` (51, 123행)
- Modify: `backend/pathfinder/routes/projects.py` (60행 + Task 3의 `_validate_language`)
- Modify: `frontend/components/admin/{ModelTable,AddModelModal,InviteUserModal,UserTable}.tsx`
- Modify: `frontend/lib/i18n/ko.ts`, `en.ts`
- Test: 각 라우트의 기존 테스트 + `errorMessage.test.ts`

**Interfaces:**
- Consumes: `useT()` (Task 1)
- Produces:
  - 백엔드: `error_codes.py`의 상수 — `EMAIL_EXISTS`, `USER_NOT_FOUND`, `BAD_REQUEST`, `FORBIDDEN`, `TOO_MANY_REQUESTS`, `USER_ADMIN_FAILED`, `SELF_TARGET`, `LAST_ADMIN`, `USER_CREATE_FAILED`, `NAME_REQUIRED`, `MODEL_ID_REQUIRED`, `MODEL_ID_CHARSET`, `MODEL_NOT_SELECTABLE`, `LANGUAGE_UNSUPPORTED`, `BUILD_SLOTS_BUSY`, `INIT_INCOMPLETE`, `BUILD_SESSION_ACTIVE`, `SURVEY_CLOSED`, `SURVEY_FULL`
  - 프론트: `function errorMessage(t, detail: string): string`

코드는 `detail` 필드로 그대로 나간다 — 새 응답 필드를 만들지 않는다. 프론트가 모르는 값은 원문 그대로 표시하므로, 코드화하지 않은 에러도 계속 읽을 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 프론트 번역기**

`frontend/lib/api/errorMessage.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { errorMessage } from "./errorMessage";
import { dictFor, type Dict } from "@/lib/i18n";

function tFor(locale: "ko" | "en") {
  const dict = dictFor(locale);
  return (key: keyof Dict) => dict[key];
}

describe("errorMessage", () => {
  it("아는 코드를 UI 언어로 번역한다", () => {
    expect(errorMessage(tFor("ko"), "email_exists")).toBe("이미 등록된 이메일입니다.");
    expect(errorMessage(tFor("en"), "email_exists")).toBe("That email is already registered.");
  });

  it("모르는 코드는 원문을 그대로 보여준다", () => {
    // 코드화하지 않은 에러가 빈 화면이 아니라 읽을 수 있는 무언가로 보여야 한다.
    expect(errorMessage(tFor("en"), "some_new_error")).toBe("some_new_error");
  });

  it("백엔드가 여전히 한국어 문장을 보내면 그대로 보여준다", () => {
    // 코드화가 부분적으로 진행된 중간 상태에서도 화면이 깨지지 않는다.
    const sentence = "무언가 실패했습니다.";
    expect(errorMessage(tFor("en"), sentence)).toBe(sentence);
  });

  it("빈 detail은 일반 실패 문구가 된다", () => {
    expect(errorMessage(tFor("en"), "")).toBe("The request failed.");
    expect(errorMessage(tFor("ko"), "")).toBe("요청이 실패했습니다.");
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/api/errorMessage.test.ts`
Expected: FAIL — `Failed to resolve import "./errorMessage"`

- [ ] **Step 3: 백엔드 코드 상수를 만든다**

`backend/pathfinder/error_codes.py`:

```python
# backend/pathfinder/error_codes.py — HTTP detail로 나가는 안정적 코드.
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
```

- [ ] **Step 4: 프론트 번역기를 만든다**

`frontend/lib/api/errorMessage.ts`:

```typescript
// frontend/lib/api/errorMessage.ts — 백엔드 에러 코드 → UI 문구.
//
// 백엔드는 UI 언어를 모르므로 안정적 코드(backend/pathfinder/error_codes.py)를
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
```

- [ ] **Step 5: 딕셔너리에 에러 키를 추가한다**

`frontend/lib/i18n/ko.ts`에 추가:

```typescript
  "err.generic": "요청이 실패했습니다.",
  "err.emailExists": "이미 등록된 이메일입니다.",
  "err.userNotFound": "사용자를 찾을 수 없습니다.",
  "err.badRequest": "요청이 올바르지 않습니다.",
  "err.forbidden": "권한이 없습니다.",
  "err.tooManyRequests": "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
  "err.userAdminFailed": "사용자 관리 요청이 실패했습니다.",
  "err.userCreateFailed": "사용자 생성에 실패했습니다. 다시 시도해 주세요.",
  "err.selfTarget": "자신의 계정에는 이 작업을 할 수 없습니다. 다른 관리자에게 요청하세요.",
  "err.lastAdmin": "마지막 관리자에게는 이 작업을 할 수 없습니다. 먼저 다른 관리자를 지정하세요.",
  "err.nameRequired": "이름을 입력하세요.",
  "err.modelIdRequired": "모델 ID를 입력하세요.",
  "err.modelIdCharset": "모델 ID는 영숫자, '.', '-', '_', ':'만 포함해야 합니다.",
  "err.modelNotSelectable": "선택할 수 없는 모델입니다.",
  "err.languageUnsupported": "지원하지 않는 언어입니다.",
  "err.buildSlotsBusy": "다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요.",
  "err.buildSessionActive": "빌드 세션이 진행 중입니다 — 세션을 먼저 종료해 주세요.",
  "err.initIncomplete": "초기화가 완료되지 않았습니다 — 다시 시도해 주세요.",
  "err.surveyClosed": "이 설문은 마감되었습니다.",
  "err.surveyFull": "응답 수 상한에 도달했습니다. 설문을 마감해 주세요.",
```

`frontend/lib/i18n/en.ts`에 추가:

```typescript
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
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/api/errorMessage.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 7: 백엔드 라우트의 `detail`을 코드로 바꾼다**

각 파일에서 `from pathfinder import error_codes as ec`를 임포트하고 아래처럼 바꾼다.

`routes/admin_users.py`:
- 44-51행 `_ERROR_DETAIL`을 코드 맵으로 교체:
```python
# status → 안정적 에러 코드. 문구는 프론트가 소유한다(error_codes.py 헤더 참조).
_ERROR_DETAIL = {
    409: ec.EMAIL_EXISTS,
    404: ec.USER_NOT_FOUND,
    400: ec.BAD_REQUEST,
    403: ec.FORBIDDEN,
    429: ec.TOO_MANY_REQUESTS,
    500: ec.USER_ADMIN_FAILED,
}
```
- 62행: `detail=_ERROR_DETAIL.get(status, ec.USER_ADMIN_FAILED))`
- 100행: `detail=ec.SELF_TARGET)` — **`what` 파라미터를 쓰지 않는다.** 조작 종류는 사용자가 누른 버튼으로 이미 화면에 드러나 있다.
- 114행: `detail=ec.LAST_ADMIN)`
- 159행: `detail=ec.USER_CREATE_FAILED) from exc`
- `_guard_privilege_removal`의 `what: str` 파라미터를 **제거하고** 호출부 3곳(187, 198, 220행)의 인자도 지운다. 그 파라미터의 유일한 용도가 문구 조립이었다:
```python
def _guard_privilege_removal(cognito, username: str, me: Principal) -> None:
```

`routes/models.py`: 102행 `ec.NAME_REQUIRED`, 104행 `ec.MODEL_ID_REQUIRED`, 107행 `ec.MODEL_ID_CHARSET`(문장 이어붙이기 제거), 121행 `ec.NAME_REQUIRED`

`routes/prototypes.py`: 258행 `ec.BUILD_SLOTS_BUSY`, 521행 `ec.BUILD_SESSION_ACTIVE`, 430행은 진단 정보를 콜론으로:
```python
            detail=f"{ec.INIT_INCOMPLETE}:{','.join(failures)}")
```

`routes/surveys_public.py`: 51행 `ec.SURVEY_CLOSED`, 123행 `ec.SURVEY_FULL`

`routes/projects.py`: 60행 `ec.MODEL_NOT_SELECTABLE`, Task 3에서 만든 `_validate_language`의 `detail`을 `ec.LANGUAGE_UNSUPPORTED`로

- [ ] **Step 8: 백엔드 테스트를 코드로 고친다**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -20`

한국어 `detail`을 단정하는 테스트가 실패한다. 각 단정을 코드로 바꾼다. 예:

```python
# 이전
assert r.json()["detail"] == "선택할 수 없는 모델입니다."
# 이후
assert r.json()["detail"] == "model_not_selectable"
```

Task 3에서 만든 `test_routes_projects_language.py`의 400 테스트에도 단정을 추가한다:

```python
    assert r.json()["detail"] == "language_unsupported"
```

- [ ] **Step 9: 백엔드 전체 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 전부 통과

- [ ] **Step 10: 프론트의 `err.detail` 렌더 4곳을 고친다**

`ModelTable.tsx`, `AddModelModal.tsx`, `InviteUserModal.tsx`, `UserTable.tsx`에서 각각 `useT()`를 얻고(`import { useT } from "@/lib/i18n/provider";`, 컴포넌트 본문에 `const t = useT();`), `errorMessage`를 임포트해 교체한다:

```typescript
// 이전
setError(err instanceof ApiError ? err.detail : "요청이 실패했습니다.");
// 이후
setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
```

`AddModelModal`의 `"모델 추가에 실패했습니다."`와 `InviteUserModal`의 `"초대에 실패했습니다."`, `UserTable`의 `"재설정에 실패했습니다."`는 각각 `t("err.generic")`으로 통일한다 — 네트워크 오류(ApiError가 아닌 경우)에 세 가지 다른 문구를 유지할 이유가 없고, 그 구분은 사용자가 방금 누른 버튼으로 이미 드러나 있다.

`CreateProjectForm.tsx`의 409 분기(`"이미 존재하는 프로젝트 ID입니다."`)는 이 태스크에서 건드리지 않는다 — 그 문구는 백엔드 `detail`이 아니라 프론트가 status로 만드는 것이므로 Task 8(UI 전수 치환)에서 딕셔너리로 옮긴다.

- [ ] **Step 11: 프론트 테스트를 확인하고 고친다**

Run: `cd frontend && npx vitest run components/admin/ 2>&1 | tail -15`

한국어 `detail`을 목으로 보내고 화면에서 그 문구를 찾는 테스트가 실패한다. 목의 `detail`을 코드로 바꾸고 단정은 한국어 문구를 유지한다(기본 로케일이 `ko`이므로):

```typescript
// 이전
http.post(`${API_BASE_URL}/admin/models`, () =>
  HttpResponse.json({ detail: "모델 ID를 입력하세요." }, { status: 422 })),
// 이후 — 백엔드가 실제로 보내는 것을 목이 흉내내야 한다
http.post(`${API_BASE_URL}/admin/models`, () =>
  HttpResponse.json({ detail: "model_id_required" }, { status: 422 })),
// 단정은 그대로: expect(screen.getByText("모델 ID를 입력하세요.")).toBeInTheDocument();
```

- [ ] **Step 12: 타입 검사 + 양쪽 전체**

Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -4`
Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 양쪽 전부 통과

- [ ] **Step 13: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/error_codes.py backend/pathfinder/routes/ backend/tests/ \
        frontend/lib/api/errorMessage.ts frontend/lib/api/errorMessage.test.ts \
        frontend/components/admin/ frontend/lib/i18n/ko.ts frontend/lib/i18n/en.ts
git commit -m "refactor(errors): 백엔드 에러 문구를 코드로, 문구는 프론트 딕셔너리가 소유

백엔드는 UI 언어를 모른다 — 프록시가 Accept-Language를 전달하지 않고, 전달해도
브라우저 값이 pf_lang 쿠키와 어긋난다. 백엔드에 두 번째 번역 시스템을 만들지
않고 안정적 코드를 보낸다.

모르는 코드는 원문 폴백이므로 코드화가 부분적으로 진행된 중간 상태에서도 화면이
빈 채로 남지 않는다."
```

---

## Task 8: 중단 마커 — 기계 신호를 언어 중립으로

**Files:**
- Modify: `backend/pathfinder/agent/claude_driver.py:730-735`
- Modify: `frontend/lib/useWorkspaceStream.ts:156`
- Test: `backend/tests/test_claude_driver.py`, `frontend/lib/useWorkspaceStream.test.tsx`

**Interfaces:**
- Consumes: 없음
- Produces: `claude_driver.INTERRUPTED_MARKER = "interrupted"` — `status` 이벤트의 `text`로 나가는 값

`claude_driver.py:735`가 `AgentEvent(kind="status", text="중단됨")`을 큐에 넣고 `useWorkspaceStream.ts:156`이 **그 문자열을 비교해** `interrupted` 플래그를 세운다. 백엔드 문구를 번역하면 프론트가 중단을 인지하지 못한다.

**Task 5의 승인 마커와 반대 결론이다.** 이것은 순수한 기계 신호다 — 에이전트가 읽지 않고 트랜스크립트에도 남지 않는다(라이브 SSE 큐에만 있다). 그래서 언어 중립 마커가 맞고, 화면 문구는 프론트가 `interrupted` 플래그로 이미 그린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 백엔드**

`backend/tests/test_claude_driver.py`에서 `"중단됨"`을 단정하는 테스트를 찾는다:

Run: `cd backend && grep -n "중단됨" tests/test_claude_driver.py`

찾은 단정을 아래로 바꾸고, 새 테스트를 하나 추가한다:

```python
def test_interrupt_marker_is_language_neutral():
    """프론트가 이 문자열을 비교해 interrupted를 세운다
    (frontend/lib/useWorkspaceStream.ts). 한국어로 두면 UI를 번역할 때
    프론트가 중단을 인지하지 못한다 — 화면에 '중단됨' 한 줄이 안 뜨고 턴이
    성공한 것처럼 보인다."""
    from pathfinder.agent.claude_driver import INTERRUPTED_MARKER
    assert INTERRUPTED_MARKER == "interrupted"
    # 사람이 읽는 문구가 아니므로 비ASCII가 없어야 한다.
    assert INTERRUPTED_MARKER.isascii()
```

기존 단정은 리터럴 대신 상수를 쓰게 고친다:

```python
    from pathfinder.agent.claude_driver import INTERRUPTED_MARKER
    assert any(e.kind == "status" and e.text == INTERRUPTED_MARKER for e in events)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py -q -k interrupt 2>&1 | tail -8`
Expected: FAIL — `ImportError: cannot import name 'INTERRUPTED_MARKER'`

- [ ] **Step 3: `claude_driver.py`를 고친다**

`_INTERRUPTED_TERMINAL_REASONS` 정의 뒤(101행 근처)에 상수를 추가:

```python
#: 중단된 턴을 표시하는 `status` 이벤트의 text. **기계 신호이고 사람이 읽는
#: 문구가 아니다** — frontend/lib/useWorkspaceStream.ts가 이 값을 비교해
#: interrupted 플래그를 세우고, 화면 문구("중단됨"/"Interrupted")는 프론트가
#: UI 언어로 그린다.
#:
#: 언어 중립인 이유가 승인 마커(frontend/lib/approvalMarker.ts)와 다르다는 점에
#: 주의: 저쪽은 에이전트에게 가고 트랜스크립트에 사용자 말풍선으로 남으므로
#: 프로젝트 언어의 단어여야 한다. 이 마커는 라이브 SSE 큐에만 있고 아무도
#: 읽지 않는다.
INTERRUPTED_MARKER = "interrupted"
```

735행의 주석과 이벤트를 교체:

```python
        # 중단 사실을 남긴다. 새 kind를 만들지 않고 기존 status로 흘리는 이유는
        # 프론트가 이미 다루는 이벤트 모양을 재사용하기 위해서다 — 프론트는 이
        # 마커를 보고 그 턴에 "중단됨" 한 줄을 UI 언어로 그린다. 이 마커는
        # 라이브 SSE 큐에만 있다 — 트랜스크립트에 들어가지 않으므로 새로고침 후
        # 복원되지 않는다.
        self._queue.append(AgentEvent(kind="status", text=INTERRUPTED_MARKER))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py -q`
Expected: PASS

- [ ] **Step 5: 실패하는 테스트를 쓴다 — 프론트**

`frontend/lib/useWorkspaceStream.test.tsx`에서 `"중단됨"` status를 흘려보내는 테스트를 찾는다:

Run: `cd frontend && grep -n "중단됨" lib/useWorkspaceStream.test.tsx`

그 목의 이벤트 텍스트를 `"interrupted"`로 바꾸고, 아래 테스트를 추가한다:

```typescript
  it("한국어 마커를 더 이상 중단으로 보지 않는다", () => {
    // 백엔드가 언어 중립 마커를 보내므로, 한국어 문자열은 이제 평범한 status
    // 트레이스다. 둘 다 받으면 에이전트가 우연히 '중단됨'이라고 말한 도구
    // 이름까지 중단으로 세게 된다.
    const it0 = reduce(baseItem, { kind: "status", text: "중단됨" });
    expect(it0.interrupted).toBeFalsy();
    expect(it0.trace).toHaveLength(1);
  });
```

이 파일의 실제 리듀서 호출 방식은 기존 테스트를 따른다 — `reduce`/`baseItem`은 그 파일이 이미 쓰는 헬퍼 이름으로 맞춘다: `cd frontend && sed -n '1,40p' lib/useWorkspaceStream.test.tsx`

- [ ] **Step 6: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/useWorkspaceStream.test.tsx`
Expected: FAIL — `"interrupted"` 마커가 `interrupted` 플래그를 세우지 않는다

- [ ] **Step 7: `useWorkspaceStream.ts`를 고친다**

156행을 교체한다. 파일 상단에 상수를 둔다:

```typescript
// 백엔드 claude_driver.INTERRUPTED_MARKER와 같은 값이어야 한다. 기계 신호이고
// 사람이 읽는 문구가 아니다 — 화면의 "중단됨"은 이 플래그를 받은 컴포넌트가
// UI 언어로 그린다.
const INTERRUPTED_MARKER = "interrupted";
```

```typescript
        if (ev.kind === "status" && ev.text === INTERRUPTED_MARKER) {
          return { ...it, interrupted: true };
        }
```

주석(151-155행)에서 "중단됨"을 마커 이름으로 바꾼다:

```typescript
        // 중단은 turn의 종결 사유라 trace가 아니라 전용 필드로 간다.
        // 드라이버가 새 kind 대신 status로 흘리는 이유는 이미 다루는 이벤트
        // 모양을 재사용하기 위해서다(claude_driver.interrupt). 이 마커는
        // 라이브 스트림에만 있다 — 트랜스크립트에는 남지 않으므로 새로고침
        // 후에는 이 줄이 다시 나타나지 않는다.
```

- [ ] **Step 8: 중단 문구를 렌더하는 곳을 확인한다**

Run: `cd frontend && grep -rn "interrupted" components/ --include='*.tsx' | grep -v '\.test\.'`

`interrupted` 플래그로 화면에 문구를 그리는 컴포넌트가 있으면 그 문구를 Task 9의 딕셔너리 치환 대상에 포함시킨다. 없으면(플래그만 쓰고 문구가 없으면) 이 태스크에서 할 일은 없다 — Task 9가 화면 전수 치환에서 다룬다.

- [ ] **Step 9: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/useWorkspaceStream.test.tsx`
Expected: PASS

- [ ] **Step 10: 양쪽 전체**

Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -4`
Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 양쪽 전부 통과

- [ ] **Step 11: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/agent/claude_driver.py backend/tests/test_claude_driver.py \
        frontend/lib/useWorkspaceStream.ts frontend/lib/useWorkspaceStream.test.tsx
git commit -m "fix(stream): 중단 마커를 언어 중립으로

status text='중단됨'을 프론트가 리터럴 비교해 interrupted를 세우고 있었다 —
UI를 번역하면 중단이 인지되지 않고 턴이 성공한 것처럼 보인다.

승인 마커와 반대 결론인 이유: 이 마커는 라이브 SSE 큐에만 있고 에이전트도
트랜스크립트도 읽지 않는 순수한 기계 신호다."
```

---

## Task 9: UI 문자열 전수 치환

**Files:** 65개 파일 — 아래 배치별로 나눠 커밋한다
- Modify: `frontend/lib/i18n/ko.ts`, `en.ts` (배치마다 키 추가)
- Test: 각 배치의 기존 테스트가 그대로 통과해야 한다 (기본 로케일 `ko`)

**Interfaces:**
- Consumes: `useT()` (Task 1), `errorMessage()` (Task 7)
- Produces: 새 키 없음 — 이 태스크는 리터럴을 키로 옮기는 작업이다

**규율 — 모든 배치에 적용한다:**

1. **`"use client"`가 없는 파일에는 추가한다.** 이미 클라이언트 컴포넌트이므로(스펙 §5) 동작 변화 없이 훅 사용이 허용된다.
2. **키 이름은 `영역.용도`.** 예: `review.gateTitle`, `proto.buildStart`, `admin.inviteEmail`.
3. **기존 테스트의 한국어 단정은 고치지 않는다.** 기본 로케일이 `ko`이므로 통과한다. 통과하지 않으면 키 연결이 틀린 것이다 — 그것이 이 단정들의 값이다.
4. **주석의 한국어는 번역하지 않는다.** 코드 주석은 개발자용이고 이 프로젝트의 개발 언어는 한국어다.
5. **`aria-label`·`title`·`placeholder`도 치환 대상이다.** 스크린리더 사용자에게도 언어가 맞아야 한다.
6. **배치마다 `npx tsc --noEmit && npx vitest run`을 돌리고 커밋한다.** 65개 파일을 한 커밋에 넣으면 어느 배치가 회귀를 만들었는지 찾을 수 없다.

배치 순서는 **의존이 적은 것부터**다. `lib/`의 스트림 훅이 먼저인 이유는 그것이 만드는 에러 문구를 여러 화면이 렌더하므로, 나중에 하면 같은 문구를 두 곳에서 고치게 된다.

- [ ] **Step 1: 배치 A — `lib/` 스트림 훅과 요약 (13줄, 5파일)**

대상: `lib/usePrototypeStream.ts`, `lib/useTurnStream.ts`, `lib/useWorkspaceStream.ts`, `lib/answerSummary.ts`

이 파일들은 훅이므로 `useT()`를 직접 쓸 수 있다. 각 파일에서:

```typescript
// 이전
if (ev.kind === "error") return { ...it, error: ev.text ?? "턴 처리 중 오류가 발생했습니다." };
// ...
error: it.error ?? "연결이 끊어졌습니다. 다시 시도해 주세요.",

// 이후 — 훅 본문 상단에서 const t = useT();
if (ev.kind === "error") return { ...it, error: ev.text ?? t("stream.turnError") };
// ...
error: it.error ?? t("stream.disconnected"),
```

**주의:** 리듀서가 훅 본문 밖의 순수 함수면 `t`를 인자로 받게 고친다 — 순수 함수 안에서 훅을 부를 수 없다. 기존 구조를 확인한다: `cd frontend && grep -n "^function\|^export function\|const reduce" lib/useWorkspaceStream.ts | head`

`lib/answerSummary.ts`의 `EMPTY = "답변 제출"`은 `answerSummary(file, answers, t)`로 `t`를 받게 한다 — 순수 함수를 훅으로 만들지 않는다. 호출부 2곳(`usePrototypeStream.ts:265`, `useWorkspaceStream.ts:246`)에서 `t`를 넘긴다.

딕셔너리에 추가할 키:

```typescript
// ko.ts
  "stream.turnError": "턴 처리 중 오류가 발생했습니다.",
  "stream.buildError": "빌드 중 오류가 발생했습니다.",
  "stream.disconnected": "연결이 끊어졌습니다. 다시 시도해 주세요.",
// en.ts
  "stream.turnError": "Something went wrong while processing this turn.",
  "stream.buildError": "Something went wrong during the build.",
  "stream.disconnected": "The connection dropped. Please try again.",
```

`answerSummary.ts`의 `EMPTY`는 **Task 6이 이미 만든 `chat.answersSubmitted`를 재사용한다** — 새 키를 만들지 않는다. 값이 같은 키를 두 개 두면 나중에 한쪽만 고쳐 화면에서 문구가 갈린다.

Run: `cd frontend && npx tsc --noEmit && npx vitest run lib/ 2>&1 | tail -4`
Expected: 통과

```bash
git add frontend/lib/ && git commit -m "i18n: 스트림 훅과 답변 요약 문구를 딕셔너리로"
```

- [ ] **Step 2: 배치 B — `components/canvas` (74줄, 14파일)**

대상: `ActivityIndicator`, `AiMessage`, `ArtifactCard`, `CanvasRightPanel`, `CanvasSidebar`, `ChatInput`, `ChatTimeline`, `ClarificationCard`, `DocumentView`, `HistorySkeleton`, `PreviewPanel`, `QuestionCardSlot`, `QuestionSummaryCard`, `ReasoningTrace`

각 파일에서 화면에 보이는 한국어 리터럴을 `t("canvas.*")` 키로 옮긴다. `"use client"`가 없는 파일(`AiMessage`, `ArtifactCard`, `CanvasRightPanel`, `CanvasSidebar`, `ClarificationCard`, `HistorySkeleton`, `PreviewPanel`, `ReasoningTrace`, `UserMessage`)에는 1행에 추가한다.

`ActivityIndicator.tsx`가 63줄로 가장 많다 — 도구 이름을 사람이 읽는 문구로 바꾸는 맵이 있다. 그 맵의 값을 딕셔너리 키로 옮기고 맵은 `도구이름 → 키` 형태로 만든다:

```typescript
// 이전
const LABEL: Record<string, string> = { Write: "파일을 쓰고 있습니다", ... };
// 이후
const LABEL_KEY: Record<string, keyof Dict> = { Write: "canvas.toolWrite", ... };
// 렌더에서: t(LABEL_KEY[name] ?? "canvas.toolGeneric")
```

Run: `cd frontend && npx tsc --noEmit && npx vitest run components/canvas/ 2>&1 | tail -4`
Expected: 통과 — 기존 한국어 단정이 `ko` 폴백으로 통과한다

```bash
git add frontend/components/canvas/ frontend/lib/i18n/ && git commit -m "i18n: canvas 컴포넌트 14개"
```

- [ ] **Step 3: 배치 C — `components/admin` + `app/admin` (80줄, 7파일)**

대상: `admin/AddModelModal`, `admin/InviteUserModal`, `admin/ModelTable`, `admin/TempPasswordPanel`, `admin/UserTable`, `app/admin/users/page.tsx`, `app/admin/models/page.tsx`

Task 7이 이 파일들의 **에러 문구**를 이미 `errorMessage(t, ...)`로 바꿨다. 이 배치는 남은 화면 문자열(라벨, 버튼, 표 헤더, 확인 문구)을 다룬다.

`UserTable.tsx`의 `ROLE_LABEL`(`{ admin: "관리자", pm: "PM" }`)과 `UserMenu.tsx`의 같은 맵은 키로 옮긴다 — `"PM"`은 두 언어에서 같지만 딕셔너리에 넣는다(값이 같은 것은 문제가 아니고, 빼면 어느 문자열이 의도적으로 번역 대상이 아닌지 알 수 없다).

Run: `cd frontend && npx tsc --noEmit && npx vitest run components/admin/ app/admin/ 2>&1 | tail -4`

```bash
git add frontend/components/admin/ frontend/app/admin/ frontend/lib/i18n/ && git commit -m "i18n: 관리자 화면"
```

- [ ] **Step 4: 배치 D — `components/prototypes` (54줄, 4파일)**

대상: `BuildPanel`(50줄), `PrototypeCard`, `SurveyDashboard`, `SurveyPanel`

Run: `cd frontend && npx tsc --noEmit && npx vitest run components/prototypes/ 2>&1 | tail -4`

```bash
git add frontend/components/prototypes/ frontend/lib/i18n/ && git commit -m "i18n: 프로토타입 탭"
```

- [ ] **Step 5: 배치 E — `app/projects` 4개 페이지 (44줄)**

대상: `dashboard/page.tsx`, `workspace/page.tsx`, `review/page.tsx`(21줄), `prototypes/page.tsx`(25줄)

`review/page.tsx`의 승인 완료 배너(`"✓ 승인 완료"`, `"이 문서로 Discovery 단계가 확정되었습니다..."`)를 키로 옮긴다. **`sendTurn`에 넘기는 텍스트는 건드리지 않는다** — Task 5가 `approvalTurnText(language)`로 이미 처리했고, 그것은 UI 언어가 아니라 프로젝트 언어다.

Run: `cd frontend && npx tsc --noEmit && npx vitest run 'app/projects/' 2>&1 | tail -4`

```bash
git add frontend/app/projects/ frontend/lib/i18n/ && git commit -m "i18n: 프로젝트 4개 페이지"
```

- [ ] **Step 6: 배치 F — `components` 루트 + `workspace` + `review` + `questions` + `dashboard` + `survey` (105줄, 21파일)**

대상: `CreateProjectForm`(14줄 — 409 문구 포함), `ProjectList`, `Markdown`, `UserMenu`, `workspace/*`(5파일), `review/*`(4파일), `questions/*`(4파일), `dashboard/*`(4파일), `survey/SurveyForm`

`CreateProjectForm.tsx`의 status 기반 문구를 키로 옮긴다:

```typescript
// 이전
if (err instanceof ApiError && err.status === 409) {
  setError("이미 존재하는 프로젝트 ID입니다.");
} else if (err instanceof ApiError) {
  setError(`프로젝트 생성에 실패했습니다. (${err.status})`);
} else {
  setError("네트워크 오류로 프로젝트를 생성하지 못했습니다.");
}
// 이후
if (err instanceof ApiError && err.status === 409) {
  setError(t("project.idExists"));
} else if (err instanceof ApiError) {
  setError(`${t("project.createFailed")} (${err.status})`);
} else {
  setError(t("project.createNetworkError"));
}
```

**언어 선택 셀렉트를 `CreateProjectForm`에 추가한다.** 이것이 Task 3의 `language` 필드가 실제로 쓰이는 자리다:

```typescript
  const [language, setLanguage] = useState<Locale>("ko");
  // ...
      <div className="sm:w-36">
        <label htmlFor="plang" className="block text-xs text-slate-500 mb-1">
          {t("project.language")}
        </label>
        {/* 생성 후 바꿀 수 없다 — 진행 중에 바꾸면 이미 만들어진 문서와
            트랜스크립트가 이전 언어로 남아 한 프로젝트 안에서 섞인다. */}
        <select
          id="plang"
          value={language}
          onChange={(e) => setLanguage(e.target.value as Locale)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          <option value="ko">한국어</option>
          <option value="en">English</option>
        </select>
      </div>
```

`handleSubmit`의 `createProject` 호출에 `language`를 넘기고, `lib/api/client.ts`의 `createProject`에 파라미터를 추가한다:

```typescript
export async function createProject(projectId: string, name?: string,
                                    modelId?: string,
                                    language?: string): Promise<ProjectSummary> {
  const body: { project_id: string; name?: string; model_id?: string;
                language?: string } = { project_id: projectId };
  if (name !== undefined) body.name = name;
  // 미지정은 키를 아예 빼서 보낸다 — 서버의 optional 필드와 맞고, null을 보내는
  // 것과 결과가 같으므로 더 적게 보내는 쪽을 고른다.
  if (modelId !== undefined) body.model_id = modelId;
  if (language !== undefined) body.language = language;
  return request<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) });
}
```

`CreateProjectForm.test.tsx`에 테스트를 추가한다:

```typescript
it("고른 언어를 생성 요청에 실어 보낸다", async () => {
  let body: unknown = null;
  server.use(
    http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ project_id: "p1", name: null, model_id: null,
                                 language: "en" });
    }),
  );
  render(<CreateProjectForm onCreated={() => {}} />);
  await userEvent.type(screen.getByLabelText(/프로젝트 ID/), "p1");
  await userEvent.selectOptions(screen.getByLabelText(/문서 언어/), "en");
  await userEvent.click(screen.getByRole("button", { name: /프로젝트 생성/ }));
  await waitFor(() => expect(body).toMatchObject({ project_id: "p1", language: "en" }));
});

it("언어를 고르지 않으면 ko로 보낸다", async () => {
  let body: unknown = null;
  server.use(
    http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ project_id: "p2", name: null, model_id: null,
                                 language: "ko" });
    }),
  );
  render(<CreateProjectForm onCreated={() => {}} />);
  await userEvent.type(screen.getByLabelText(/프로젝트 ID/), "p2");
  await userEvent.click(screen.getByRole("button", { name: /프로젝트 생성/ }));
  await waitFor(() => expect(body).toMatchObject({ language: "ko" }));
});
```

딕셔너리에 추가할 키:

```typescript
// ko.ts
  "project.language": "문서 언어",
  "project.idExists": "이미 존재하는 프로젝트 ID입니다.",
  "project.createFailed": "프로젝트 생성에 실패했습니다.",
  "project.createNetworkError": "네트워크 오류로 프로젝트를 생성하지 못했습니다.",
// en.ts
  "project.language": "Document language",
  "project.idExists": "That project ID already exists.",
  "project.createFailed": "Could not create the project.",
  "project.createNetworkError": "A network error prevented creating the project.",
```

`project.createFailed`의 한국어에서 `(${err.status})`를 뺀 것에 주의 — 상태 코드는 호출부가 붙인다(`${t("project.createFailed")} (${err.status})`). 딕셔너리 값에 포맷 자리를 두지 않는 이유는 두 언어에서 자리 순서가 달라질 수 있고, 그때 조립이 조용히 어긋나기 때문이다.

Run: `cd frontend && npx tsc --noEmit && npx vitest run components/ 2>&1 | tail -4`

```bash
git add frontend/components/ frontend/lib/api/client.ts frontend/lib/i18n/ && \
  git commit -m "i18n: 나머지 컴포넌트 + 프로젝트 생성 폼의 언어 선택"
```

- [ ] **Step 7: 배치 G — `app/login`, `app/survey`, `app/page.tsx` (24줄)**

대상: `app/login/page.tsx`(8줄), `app/survey/[token]/page.tsx`(11줄), `app/page.tsx`(5줄)

`app/survey/[token]/page.tsx`는 **공개 페이지**다(응답자가 토큰 링크로 들어온다, 인증 없음). 스펙 §범위 밖에 따라 **설문 문항의 언어를 따라야** 하지만, 이 태스크에서는 UI 로케일(쿠키)을 쓴다 — 응답자는 쿠키가 없어 `ko`가 되고, 그것이 현재 동작이다. 설문 응답 화면의 언어를 문항 언어에 맞추는 것은 Task 12에서 다룬다.

Run: `cd frontend && npx tsc --noEmit && npx vitest run app/ 2>&1 | tail -4`

```bash
git add frontend/app/ frontend/lib/i18n/ && git commit -m "i18n: 로그인·설문·프로젝트 목록 페이지"
```

- [ ] **Step 8: 남은 한국어 리터럴이 없음을 확인한다**

Run:
```bash
cd frontend && python3 - <<'PY'
import re, pathlib
KO = re.compile(r'[가-힣]')
left = []
for d in ('app', 'components', 'lib'):
    for p in pathlib.Path(d).rglob('*.ts*'):
        if '.test.' in p.name: continue
        txt = p.read_text(encoding='utf-8')
        body = re.sub(r'/\*.*?\*/', '', txt, flags=re.S)
        body = '\n'.join(l for l in body.splitlines() if not l.strip().startswith('//'))
        body = re.sub(r'//.*$', '', body, flags=re.M)
        for i, l in enumerate(body.splitlines(), 1):
            if KO.search(l):
                left.append(f"{p}:{i}: {l.strip()[:80]}")
print(f"남은 줄: {len(left)}")
for x in left: print("  ", x)
PY
```

Expected: 남는 것은 **의도적인 것뿐**이다. 아래는 남아야 한다:
- `lib/i18n/ko.ts` 전체 (한국어 딕셔너리)
- `lib/approvalMarker.ts`의 `"승인"` (프로젝트 언어 단어)
- `lib/approvalState.ts`의 `/수정|revise|.../` (양쪽 언어를 받는 판정식)
- `components/LanguageSwitcher.tsx`의 `"한국어"` (언어는 그 언어로 표기)
- `components/AppHeader.tsx`의 `LANGUAGE_LABEL`, `aria-label="Language / 언어"`
- `app/layout.tsx`의 `description: "AI-PLC Discovery 웹 서비스"` — `metadata`는 서버에서 정적으로 평가되어 `useT()`를 쓸 수 없다. 이 문자열은 브라우저 탭이 아니라 `<meta>`에만 나가므로 그대로 둔다.
- `components/canvas` 등의 `LABEL_KEY` 맵에 남은 한국어가 **있으면 안 된다** — 있으면 치환이 덜 된 것이다.

목록에 위 예외 밖의 것이 남아 있으면 그 파일을 고치고 해당 배치의 커밋에 amend한다.

- [ ] **Step 9: 영어 렌더 대표 테스트를 추가한다**

`frontend/lib/i18n/render.test.tsx` (신규):

```typescript
// 영어 렌더의 대표 확인. 화면마다 영어 단정을 두지 않는 이유: 그러면 딕셔너리를
// 두 번 쓰는 셈이고, 키 연결의 정확성은 기존 한국어 단정 535건이 이미 지킨다
// (기본 로케일이 ko이므로 그것들이 곧 "키가 맞게 연결됐는가" 테스트다).
// 여기서는 배관이 로케일을 실제로 갈아끼우는지만 본다.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { LocaleProvider } from "@/lib/i18n/provider";
import { AppHeader } from "@/components/AppHeader";
import { ko } from "@/lib/i18n/ko";
import { en } from "@/lib/i18n/en";

describe("영어 로케일 렌더", () => {
  it("헤더가 영어로 그려진다", () => {
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="dashboard" projectId="p1" />
      </LocaleProvider>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    // 한국어가 남아 있으면 치환이 덜 된 것이다.
    expect(screen.queryByText("대시보드")).toBeNull();
  });
});

describe("딕셔너리 완결성", () => {
  it("두 딕셔너리의 키가 같고 값이 비어 있지 않다", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ko).sort());
    for (const [k, v] of Object.entries(en)) {
      expect(v.trim(), `빈 en 값: ${k}`).not.toBe("");
    }
  });

  it("영어 딕셔너리에 한글이 없다", () => {
    // 치환 중 실수로 한국어를 en.ts에 복사한 것을 잡는다.
    const KO = /[가-힣]/;
    for (const [k, v] of Object.entries(en)) {
      expect(KO.test(v), `en.ts에 한글: ${k} = ${v}`).toBe(false);
    }
  });
});
```

Run: `cd frontend && npx vitest run lib/i18n/render.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 10: 빌드와 전체 스위트**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -8`
Run: `cd frontend && npx vitest run 2>&1 | tail -5`
Expected: 빌드 성공, 모든 테스트 통과. 기존 664가 줄지 않았는지 확인한다.

- [ ] **Step 11: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/i18n/render.test.tsx
git commit -m "test(i18n): 영어 렌더 대표 테스트 + 딕셔너리 완결성

화면마다 영어 단정을 두지 않는다 — 키 연결의 정확성은 기존 한국어 단정
535건이 이미 지킨다(기본 로케일이 ko이므로). 여기서는 배관이 로케일을
갈아끼우는지와 en.ts에 한글이 섞이지 않았는지만 본다."
```

---

## Task 10: `place_rules` 언어별 조립 — 최고 위험

**Files:**
- Create: `rule/aiplc-rules/language/ko.md`
- Create: `rule/aiplc-rules/language/en.md`
- Modify: `rule/aiplc-rules/aws-aiplc-rules/core-workflow.md:3` (삭제)
- Modify: `discovery-config/CLAUDE.md` (§번역 오버라이드 삭제)
- Modify: `proto-config/CLAUDE.md:1` (삭제)
- Modify: `backend/pathfinder/agent/workspace_rules.py`
- Modify: `backend/pathfinder/agent/claude_driver.py:546-563, 1301-1313`
- Modify: `backend/pathfinder/app.py:250-260`
- Test: `backend/tests/test_workspace_rules.py`, `backend/tests/test_claude_driver.py`, `backend/tests/test_driver_factory.py`

**Interfaces:**
- Consumes: `app_module.project_language(pid)` (Task 3)
- Produces:
  - `place_rules(workspace: str, rules_dir: str, language: str = "ko") -> None`
  - `ClaudeDriver(..., language: str = "ko")`

**이 태스크가 가장 위험하고, 실패가 조용하다.** 문서 절반이 영어로 나와도 에러는 없다. 자동 테스트는 `CLAUDE.md`가 어떻게 조립됐는지만 확인할 수 있고 모델이 그것을 따랐는지는 확인할 수 없다 — Step 12의 수동 검증이 필수다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_workspace_rules.py`의 `_rules` 픽스처에 언어 파일을 추가한다:

```python
def _rules(tmp_path: Path) -> Path:
    """리포의 rule/aiplc-rules 레이아웃을 흉내낸 픽스처."""
    rules = tmp_path / "rules"
    (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text(
        "# DISCOVERY PHASE WORKFLOW", encoding="utf-8")
    lang = rules / "language"
    lang.mkdir(parents=True)
    (lang / "ko.md").write_text("KO-DIRECTIVE", encoding="utf-8")
    (lang / "en.md").write_text("EN-DIRECTIVE", encoding="utf-8")
    details = rules / "aws-aiplc-rule-details" / "common"
    details.mkdir(parents=True)
    (details / "process-overview.md").write_text("OVERVIEW", encoding="utf-8")
    return rules
```

기존 `test_copies_core_workflow_as_claude_md`, `test_is_idempotent`, `test_overwrites_a_file_whose_size_differs`는 `CLAUDE.md`가 core-workflow **전문과 정확히 같음**을 단정하므로 조립 후에는 실패한다. 아래로 교체한다:

```python
def test_claude_md_is_language_directive_then_core_workflow(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    # 언어 지시가 **앞에** 온다. discovery-config/CLAUDE.md:34-37이 기록한
    # 실패에서 "맥락이 가까운" 템플릿의 CRITICAL이 언어 지시를 이겼으므로,
    # 여기서는 언어를 문서 전체의 전제로 맨 앞에 둔다.
    assert text.index("KO-DIRECTIVE") < text.index("# DISCOVERY PHASE WORKFLOW")
    assert "EN-DIRECTIVE" not in text


def test_english_project_gets_the_english_directive(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="en")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "EN-DIRECTIVE" in text
    # 한국어 지시가 남으면 두 지시가 충돌한다 — 이것이 7f33652의 실패 모양이다.
    assert "KO-DIRECTIVE" not in text


def test_the_two_languages_produce_different_claude_md(tmp_path):
    rules = _rules(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    place_rules(str(a), str(rules), language="ko")
    place_rules(str(b), str(rules), language="en")
    assert (a / "CLAUDE.md").read_text(encoding="utf-8") \
        != (b / "CLAUDE.md").read_text(encoding="utf-8")


def test_defaults_to_korean(tmp_path):
    # 인자를 안 주는 호출부(구 코드, 테스트)가 기존 동작을 유지한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert "KO-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_an_unknown_language_falls_back_to_korean(tmp_path):
    # 손상된 매니페스트가 임의 문자열을 실어 와도 룰 없이 돌지 않는다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="klingon")
    assert "KO-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_switching_language_rewrites_claude_md(tmp_path):
    # 조립 결과는 원본 파일이 아니므로 크기 비교 최적화를 적용하지 않는다.
    # 두 언어 지시의 크기가 우연히 같아도 반드시 다시 써야 한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    place_rules(str(ws), str(rules), language="en")
    assert "EN-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_is_idempotent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    first = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    place_rules(str(ws), str(rules), language="ko")
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == first


def test_raises_when_the_language_directive_is_missing(tmp_path):
    # core-workflow가 없을 때와 같은 규율이다: 룰 없이 조용히 진행하면
    # 에이전트가 언어를 모르는 채로 돌고, 그건 절반만 번역된 문서로 나타나
    # 원인 추적이 어렵다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    (rules / "language" / "ko.md").unlink()
    with pytest.raises(FileNotFoundError):
        place_rules(str(ws), str(rules), language="ko")
```

그리고 실제 리포 룰에 대한 통합 핀을 교체한다:

```python
def test_works_against_the_real_repo_rules():
    # 픽스처가 잘못된 레이아웃을 굳혀 실제 배치가 깨지는 것을 막는 통합 핀.
    import tempfile
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        pytest.skip("repo rules not present")
    for language in ("ko", "en"):
        with tempfile.TemporaryDirectory() as ws:
            place_rules(ws, str(repo_rules), language=language)
            assert (Path(ws) / "CLAUDE.md").is_file()
            assert (Path(ws) / "aws-aiplc-rule-details" / "common").is_dir()


def test_core_workflow_has_no_language_directive_of_its_own():
    """이 스펙의 핵심 불변식이다.

    상류 룰을 갱신하며 그 줄을 되살리면 조용히 충돌이 돌아온다 —
    영어 프로젝트에서 core-workflow의 '한국어로 진행'과 language/en.md가
    서로 반대를 말하고, 어느 쪽이 이길지 예측할 수 없다(7f33652).
    """
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    core = repo_rules / "aws-aiplc-rules" / "core-workflow.md"
    if not core.is_file():
        pytest.skip("repo rules not present")
    text = core.read_text(encoding="utf-8")
    assert "한국어로 진행" not in text


def test_shared_config_dirs_have_no_language_directive():
    """공유 CLAUDE_CONFIG_DIR은 전 프로젝트가 공유하므로 언어를 정할 수 없다.

    남겨두면 영어 프로젝트에서 워크스페이스의 language/en.md와 충돌한다.
    """
    repo = Path(__file__).resolve().parents[2]
    for rel in ("discovery-config/CLAUDE.md", "proto-config/CLAUDE.md"):
        path = repo / rel
        if not path.is_file():
            pytest.skip(f"{rel} not present")
        text = path.read_text(encoding="utf-8")
        assert "한국어로 진행" not in text, rel
        # 번역 오버라이드 절도 language/ko.md로 옮겨졌어야 한다.
        assert "번역해서 쓴다" not in text, rel
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace_rules.py -q 2>&1 | tail -10`
Expected: FAIL — `place_rules() got an unexpected keyword argument 'language'`

- [ ] **Step 3: 언어 지시 파일 두 개를 만든다**

`rule/aiplc-rules/language/ko.md`:

```markdown
# 언어 규약 (이 문서 전체의 전제)

**모든 대화, 문서작성, 질의 응답은 한국어로 진행한다.** 단 기술용어·고유명사·
파일명은 영어를 그대로 유지한다.

## 아래 워크플로우 양식의 영어 문구는 번역해서 쓴다

이 문서 뒤에 오는 워크플로우와 `aws-aiplc-rule-details/`의 문서 양식에는
**완성된 영어 문장**이 리터럴로 박혀 있다. 대표적으로 `envision.md`의 PR/FAQ
질문들이다:

```markdown
#### Q: What is the price?
A: [Answer]
```

`A:` 쪽은 `[Answer]`라는 빈 자리지만 `Q:` 쪽은 이미 영어로 완성돼 있어서, 그대로
복사하면 **질문은 영어, 답변은 한국어**인 문서가 나온다. 실제로 그렇게 나왔다:
템플릿에 있던 질문 20여 개는 영어로 남고, 에이전트가 직접 추가한 질문 하나만
한국어였다.

원인은 두 지시가 반대를 말하기 때문이다 — 위의 "모든 문서작성은 한국어"와,
템플릿 바로 앞의 `**CRITICAL**: Use the ... format exactly as defined below.
Do NOT deviate from this structure.` **그 CRITICAL은 이렇게 읽어야 한다:**

- **"exactly as defined"가 요구하는 것은 구조다** — 섹션 순서, 항목 구성, 어느
  질문이 들어가는지, 계층(`####`)과 `Q:`/`A:` 표기. 이것은 바꾸지 않는다.
- **언어는 구조가 아니다.** 질문 문구·헤딩·라벨은 **한국어로 번역해서 쓴다.**
  질문을 빼거나 순서를 바꾸거나 새로 만들라는 뜻이 아니다 — 같은 질문을 한국어로
  적으라는 뜻이다.

적용 대상은 PR/FAQ만이 아니다. `product-strategy.md`, `go-to-market.md`에도 같은
형태의 영어 리터럴이 있고(각각 십수 개), 같은 규칙을 적용한다. 즉 **양식에서
가져온 모든 사용자 노출 문구는 한국어로 옮긴다.**

영어를 그대로 두는 것은 위에서 예외로 둔 것뿐이다 — **기술용어·고유명사·
파일명**, 그리고 경로·도구 이름·코드 식별자. 예를 들어 `PROTOTYPE-{slug}.md`,
`offline-first`, `TAM`, `SaaS`는 그대로 두고, `Q: What is the price?`는
`Q: 가격은 어떻게 책정되나요?`로 적는다.

문서의 **섹션 헤딩도 같다**(`### Press Release` → `### 보도자료`,
`### External FAQs (Customer-Facing)` → `### 외부 FAQ (고객 대상)`). 단
`submit_document`가 파싱에 의존하는 파일명과 경로는 절대 번역하지 않는다.

---
```

`rule/aiplc-rules/language/en.md`:

```markdown
# Language convention (a premise for this entire document)

**Conduct all conversation, document writing, and Q&A in English.**

The workflow and the document formats under `aws-aiplc-rule-details/` are already
written in English, so follow them as they stand. There is nothing to translate.

Keep file names, paths, tool names, and code identifiers exactly as the rules
spell them — `submit_document` parses some of them, and a renamed path means the
document never reaches the review screen.

---
```

**`en.md`가 짧은 것이 정상이다.** 상류 룰의 원래 언어가 영어이므로 오버라이드할 것이 없다 — `ko.md`의 §번역 절이 존재하는 이유가 그것이다.

- [ ] **Step 4: 상류 룰과 공유 config에서 언어 지시를 삭제한다**

`rule/aiplc-rules/aws-aiplc-rules/core-workflow.md`의 3행을 **삭제한다**:

```
# 모든 대화, 문서작성, 질의 응답은 한국어로 진행합니다. 단, 기술용어나 고유명사, 파일명 등은 영어를 그대로 유지합니다.
```

`discovery-config/CLAUDE.md`에서 §"문서 양식의 영어 문구는 번역해서 쓴다"(19-56행) **전체를 삭제한다** — 내용은 `language/ko.md`로 옮겨졌다. **1행의 비ASCII 표기 규약은 남긴다** — 언어 규약처럼 보이지만 인코딩 규약이고, 영어 프로젝트에서도 한국어 파일명·기존 문서를 다룰 때 필요하다.

`proto-config/CLAUDE.md`의 1행 `# 모든 대화는 한국어로 진행`을 **삭제한다**. 2행의 비ASCII 규약과 3행의 shadcn-design 지시는 남긴다.

- [ ] **Step 5: `workspace_rules.py`를 고친다**

파일 전문을 교체:

```python
# backend/pathfinder/agent/workspace_rules.py — 상류 AI-PLC 레이아웃을
# 워크스페이스에 재현하고, 언어 지시를 그 앞에 붙인다.
#
# 상류(aws-samples/sample-ai-plc)의 Claude Code 셋업은 core-workflow.md를
# 프로젝트 루트의 CLAUDE.md로 복사하고 상세 룰을 aws-aiplc-rule-details/에 둔다.
# core-workflow.md의 `Rule details location: ./aws-aiplc-rule-details/`가
# CWD 상대경로를 전제하므로 룰은 CLAUDE_CONFIG_DIR이 아니라 워크스페이스에 있어야
# 한다 — 그래야 에이전트가 그 경로를 그대로 읽는다.
#
# 언어 지시가 **여기**로 온 이유(스펙 2026-08-03-bilingual-ko-en §3):
# CLAUDE_CONFIG_DIR은 전 프로젝트가 공유하므로 프로젝트별 언어를 담을 수 없다.
# setting_sources=["user", "project"]에서 "user"가 그 공유 디렉토리이고
# "project"가 워크스페이스이므로, 프로젝트별 언어는 이 파일이 쓰는 CLAUDE.md
# (=project 레벨)로만 흐를 수 있다.
#
# 그리고 언어 지시는 **두 곳에 있으면 안 된다.** 커밋 7f33652가 그 실패였다:
# core-workflow의 "한국어로 진행"과 템플릿의 `**CRITICAL**: ... exactly as
# defined`가 반대를 말했고, 후자가 이겨서 PR/FAQ 질문 20여 개가 영어로 남았다.
# 그래서 상류 룰 파일과 공유 config에서 언어 줄을 지우고, 유일한 출처를
# language/{ko,en}.md로 만든다(test_workspace_rules가 그 불변식을 지킨다).
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger("pathfinder.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"
_LANGUAGE_DIR = "language"

#: 지원 언어. ProjectRegistry._LANGUAGES와 같은 집합이어야 한다.
_LANGUAGES = ("ko", "en")
_DEFAULT_LANGUAGE = "ko"


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기가 같으면 건너뛴다. 룰은 읽기 전용이므로 크기 비교로 충분하고,
    매 턴 수십 개 파일을 다시 쓰지 않게 한다."""
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def place_rules(workspace: str, rules_dir: str,
                language: str = _DEFAULT_LANGUAGE) -> None:
    """`language/{lang}.md` + `core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    멱등이며 매 턴 호출해도 싸다. 룰이 없으면 FileNotFoundError — 조용히
    진행하면 에이전트가 워크플로우를 모르는 채로 돌고, 그건 빈 대화로 나타나서
    원인 추적이 어렵다. 언어 지시가 없을 때도 같은 이유로 던진다: 그 실패는
    절반만 번역된 문서로 나타나 더 찾기 어렵다.

    **언어 지시가 앞에 온다.** discovery-config/CLAUDE.md:34-37이 기록한
    실패에서 "맥락이 가까운" 템플릿의 CRITICAL이 언어 지시를 이겼으므로,
    여기서는 언어를 문서 전체의 전제로 맨 앞에 두고, ko.md가 그 CRITICAL을
    어떻게 읽어야 하는지까지 설명한다.

    알 수 없는 language는 기본값으로 떨어진다. 라우트가 생성 시점에 검증하므로
    정상 경로로는 들어올 수 없지만, 손상된 매니페스트 때문에 룰 없이 도는
    것보다 한국어로 도는 편이 낫다.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    lang = language if language in _LANGUAGES else _DEFAULT_LANGUAGE
    if lang != language:
        _log.warning("unknown project language %r — using %s", language, lang)
    directive = root / _LANGUAGE_DIR / f"{lang}.md"
    if not directive.is_file():
        raise FileNotFoundError(f"language directive not found: {directive}")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    # 조립 결과는 원본 파일이 아니므로 _copy_if_changed의 크기 비교를 쓰지
    # 않는다. 두 언어 지시의 크기가 우연히 같으면 언어를 바꿔도 파일이 그대로
    # 남는데, 그 침묵이 정확히 이 스펙이 없애려는 실패 모양이다. 파일 하나
    # 쓰기는 싸다.
    (ws / "CLAUDE.md").write_text(
        directive.read_text(encoding="utf-8") + "\n\n"
        + core.read_text(encoding="utf-8"),
        encoding="utf-8")

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # core만으로도 워크플로우는 시작된다(상세 룰은 온디맨드) — 경고만.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws / _DETAILS_DIR / src.relative_to(details))
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace_rules.py -q`
Expected: PASS (12 tests). `test_core_workflow_has_no_language_directive_of_its_own`과 `test_shared_config_dirs_have_no_language_directive`가 Step 4의 삭제를 검증한다.

- [ ] **Step 7: 실패하는 테스트를 쓴다 — `ClaudeDriver`가 언어를 넘긴다**

`backend/tests/test_claude_driver.py`에 추가:

```python
def test_driver_places_the_project_language_directive(tmp_path):
    """드라이버가 프로젝트 언어를 place_rules에 전달한다.

    이 배선이 빠지면 모든 프로젝트가 한국어 지시로 돌고, 영어를 고른 사용자는
    영어 UI로 한국어 문서를 받는다 — 에러는 없다.
    """
    from pathfinder.agent.claude_driver import ClaudeDriver
    seen = {}

    def fake_place_rules(workspace, rules_dir, language="ko"):
        seen["language"] = language

    import pathfinder.agent.claude_driver as mod
    original = mod.place_rules
    mod.place_rules = fake_place_rules
    try:
        d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                         config_dir=str(tmp_path), s3=None,
                         language="en", session_store=None)
        assert d._place_rules() is True
        assert seen["language"] == "en"
    finally:
        mod.place_rules = original


def test_driver_defaults_to_korean(tmp_path):
    from pathfinder.agent.claude_driver import ClaudeDriver
    d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                     config_dir=str(tmp_path), s3=None, session_store=None)
    assert d._language == "ko"
```

`backend/tests/test_driver_factory.py`에 추가:

```python
def test_driver_factory_passes_the_project_language(monkeypatch, tmp_path):
    """app.driver_factory가 레지스트리의 언어를 드라이버에 싣는다."""
    import pathfinder.app as app_module
    app_module.registry.register("lang-1", None, language="en")
    try:
        driver = app_module.driver_factory("lang-1", tmp_path)
        assert driver._language == "en"
    finally:
        app_module.registry.remove("lang-1")
```

- [ ] **Step 8: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_driver_factory.py -q -k language 2>&1 | tail -8`
Expected: FAIL — `ClaudeDriver() got an unexpected keyword argument 'language'`

- [ ] **Step 9: `ClaudeDriver`와 `driver_factory`를 고친다**

`claude_driver.py`의 `__init__`(546-563행)에 파라미터를 추가한다. `anthropic_model` 뒤에:

```python
    def __init__(self, workspace: str, rules_dir: str, config_dir: str,
                 s3: S3StoreLike, anthropic_model: str | None = None,
                 language: str = "ko",
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[dict], Any] | None = None,
                 session_store: Any = None):
```

본문에 저장:

```python
        # 이 프로젝트의 생성물 언어. place_rules가 이 값으로 워크스페이스
        # CLAUDE.md의 언어 지시를 고른다 — 프로젝트별 언어가 에이전트에게
        # 닿는 유일한 경로다(공유 CLAUDE_CONFIG_DIR은 담을 수 없다).
        self._language = language
```

`_place_rules`(1301-1313행)의 호출을 고친다:

```python
            place_rules(self._workspace, self._rules_dir, self._language)
```

docstring에 한 줄 추가:

```python
        """Rule placement happens every turn -- the workspace is volatile
        (runner reconstructs it from S3 each turn, and runner.py:36 restores
        only aiplc-docs/, prototype/, uploads/ -- never the rules) and without
        them the agent runs with no workflow to follow, which shows up as an
        empty conversation rather than an error. False means the turn must be
        abandoned.

        매 턴 쓰는 것이 언어에도 유리하다: 언어 지시가 워크스페이스에 남아
        있지 않아도 다음 턴에 다시 깔린다.
        """
```

`app.py`의 `driver_factory`(250-260행)에 인자를 추가:

```python
    return ClaudeDriver(
        workspace=str(local_root),
        rules_dir=_rules_dir(),
        config_dir=str(_discovery_config_dir()),
        s3=s3_store_factory(project_id),
        anthropic_model=project_model(project_id),
        language=project_language(project_id),
    )
```

`StrandsDriver` 분기는 **고치지 않는다** — 폴백 경로이고 워크숍 후 삭제 예정이다(스펙 §범위 밖). `driver.py`의 클래스 docstring에 한 줄을 남긴다:

```python
    # 프로젝트별 언어를 지원하지 않는다: 이 드라이버는 폴백 경로이고 워크숍 후
    # 삭제 예정이다(프로젝트별 모델도 같은 이유로 지원하지 않는다).
    # PATHFINDER_DISCOVERY_DRIVER=strands로 돌리면 모든 프로젝트가 한국어로
    # 돈다 — 상류 룰의 기본 언어가 아니라, 이 드라이버가 언어 지시를 조립하지
    # 않고 system_prompt를 직접 만들기 때문이다.
```

- [ ] **Step 10: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_driver_factory.py -q`
Expected: PASS

- [ ] **Step 11: 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -5`
Expected: 전부 통과. `place_rules`를 2-인자로 부르는 다른 테스트는 기본값으로 통과한다.

- [ ] **Step 12: 수동 검증 — 이 태스크의 필수 관문**

**자동 테스트로는 잡히지 않는다.** 테스트는 `CLAUDE.md`가 어떻게 조립됐는지만 확인할 수 있고, 모델이 그것을 따랐는지는 확인할 수 없다.

조립 결과를 눈으로 먼저 본다:

```bash
cd /home/ec2-user/project/pathfinder-sp/backend
.venv/bin/python - <<'PY'
import tempfile, pathlib
from pathfinder.agent.workspace_rules import place_rules
for lang in ("ko", "en"):
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, "../rule/aiplc-rules", language=lang)
        text = (pathlib.Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
        print(f"=== {lang}: {len(text)}자 ===")
        print(text[:600])
        print("...")
        # 언어 지시가 하나만 있는지 확인 — 둘이면 충돌한다.
        print("한국어로 진행:", text.count("한국어로 진행"))
        print("in English:", text.count("in English"))
PY
```

Expected: `ko`는 `한국어로 진행`이 1회, `in English`가 0회. `en`은 반대. **어느 쪽이든 2회 이상이면 중복 지시이므로 Step 4의 삭제가 덜 된 것이다.**

그다음 **실제로 프로젝트를 두 개 돌린다.** 워크숍 전에 반드시 한다:

1. 백엔드·프론트엔드를 띄우고 프로젝트를 두 개 만든다 — 하나는 언어 `한국어`, 하나는 `English`.
2. 각각에서 Discovery를 Envision 단계까지 진행한다(PR/FAQ가 생성되는 지점까지).
3. `문서 리뷰` 탭에서 아래 네 곳을 눈으로 확인한다. **이 네 곳이 `7f33652`에서 실제로 어긋났던 지점이다:**
   - `envision.md` PR/FAQ의 `Q:` 질문 문구 — 템플릿에서 온 20여 개
   - `product-strategy.md`의 표 헤딩·라벨
   - `go-to-market.md`의 표 헤딩·라벨
   - 섹션 헤딩 (`### Press Release` / `### 보도자료`)
4. 채팅 말풍선도 확인한다 — 에이전트의 대화 언어.

한국어 프로젝트에서 영어가 남아 있거나 영어 프로젝트에서 한국어가 섞이면, `language/{lang}.md`의 지시를 강화하고 3단계부터 다시 확인한다. **부분 번역을 발견하면 그 문서와 위치를 기록해 두고 커밋 메시지에 남긴다** — 다음 사람이 같은 곳을 먼저 보게 된다.

- [ ] **Step 13: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add rule/aiplc-rules/language/ rule/aiplc-rules/aws-aiplc-rules/core-workflow.md \
        discovery-config/CLAUDE.md proto-config/CLAUDE.md \
        backend/pathfinder/agent/workspace_rules.py \
        backend/pathfinder/agent/claude_driver.py backend/pathfinder/agent/driver.py \
        backend/pathfinder/app.py backend/tests/
git commit -m "feat(language): place_rules가 언어 지시를 조립한다

언어 지시의 단일 출처를 rule/aiplc-rules/language/{ko,en}.md로 만들고, 상류
core-workflow.md와 공유 CLAUDE_CONFIG_DIR 두 곳에서 언어 줄을 삭제했다.
7f33652의 실패가 두 레벨의 지시 충돌이었으므로, 출처가 하나여야 한다.

지시를 문서 맨 앞에 두는 이유: 그 실패에서 '맥락이 가까운' 템플릿의 CRITICAL이
언어 지시를 이겼다. ko.md는 그 CRITICAL을 어떻게 읽어야 하는지까지 설명한다.

en.md가 짧은 것은 정상이다 — 상류 룰의 원래 언어가 영어여서 오버라이드할 것이
없다.

수동 검증: 한국어/영어 프로젝트를 각각 돌려 PR/FAQ 질문, product-strategy와
go-to-market의 표 라벨, 섹션 헤딩, 채팅 말풍선을 확인했다."
```

---

## Task 11: 프로토타입 프롬프트 — 언어별 두 벌

**Files:**
- Create: `backend/pathfinder/proto/prompts.py`
- Modify: `backend/pathfinder/proto/session.py:139-171, 478-620`
- Modify: `backend/pathfinder/proto/tools.py`
- Modify: `backend/pathfinder/app.py:308-337` (`proto_session_factory`)
- Test: `backend/tests/test_proto_session.py`, `backend/tests/test_proto_tools.py`, `backend/tests/test_proto_prompts.py` (신규)

**Interfaces:**
- Consumes: `app_module.project_language(pid)` (Task 3)
- Produces:
  - `plan_prompt(language, *, spec_key, proxy_path) -> str`
  - `resume_prompt(language) -> str`
  - `missing_output_prompt(language, *, spec_key) -> str`
  - `handoff_prompt(language, *, spec_key, summary, remaining) -> str`
  - `build_complete_description(language) -> str`, `build_complete_rejection(language) -> str`
  - `PrototypeSession(..., language: str = "ko")`

**프롬프트를 조립하지 않는다.** 언어별로 완성된 문장 두 벌을 유지한다 — `first_prompt`의 docstring이 기록하듯 이 텍스트가 **유일한 브레이크**다(빌더는 `bypassPermissions`로 돌아 Write/Edit이 자동 승인된다). 문장을 쪼개면 "계획만 세우고 빌드하지 마"의 강도가 어느 언어에서 약해졌는지 알 수 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_proto_prompts.py` (신규):

```python
# backend/tests/test_proto_prompts.py
#
# 프롬프트는 빌드 에이전트의 유일한 브레이크다(proto/session.py의
# first_prompt docstring). 두 언어가 같은 지시를 담고 있는지 확인한다 —
# 조립이 아니라 두 벌을 유지하므로, 한쪽에만 빠진 지시가 조용히 생길 수 있다.
from __future__ import annotations

import pytest

from pathfinder.proto.prompts import (
    build_complete_description, build_complete_rejection, handoff_prompt,
    missing_output_prompt, plan_prompt, resume_prompt,
)

SPEC = "aiplc-docs/discovery/prototypes/demo/PROTOTYPE-demo.md"
PROXY = "/api/proto/p1/demo/"


def _plan(language: str) -> str:
    return plan_prompt(language, spec_key=SPEC, proxy_path=PROXY)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_plan_prompt_carries_every_directive(language):
    """두 언어 모두 같은 브레이크를 걸어야 한다. 하나라도 빠지면 그 언어의
    빌드는 승인 없이 시작되거나 산출물을 엉뚱한 곳에 둔다."""
    p = _plan(language)
    assert SPEC in p                    # 스펙을 읽으라고 지시
    assert "AskUserQuestion" in p       # 승인을 받으라고 지시
    assert "prototype/" in p            # 산출물 위치
    assert "build_complete" in p        # 완료 선언
    assert "BEDROCK_MODEL_ID" in p      # 모델 주입 이름
    assert "basePath" in p              # 하위 경로 서빙
    assert PROXY in p
    # 생성되는 앱의 화면 문구도 프로젝트 언어여야 한다(스펙 §4). 이 지시가
    # 없으면 영어 프로젝트가 한국어 UI의 프로토타입을 받는다.
    assert "i18n" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_plan_prompt_forbids_building_in_the_first_turn(language):
    p = _plan(language).lower()
    # 이 지시가 유일한 브레이크다 — 없으면 에이전트가 바로 빌드를 시작한다.
    forbid = ["빌드는 시작하지" in p or "do not start building" in p,
              "write/edit" in p or "write·edit" in p]
    assert all(forbid), p[:400]


def test_korean_prompt_is_korean_and_english_prompt_is_english():
    ko, en = _plan("ko"), _plan("en")
    assert any("가" <= c <= "힣" for c in ko)
    # 영어 프롬프트에 한글이 섞이면 번역이 덜 된 것이다. 파일 경로에는 한글이
    # 없으므로(SPEC/PROXY 모두 ASCII) 이 단정이 유효하다.
    assert not any("가" <= c <= "힣" for c in en), en


@pytest.mark.parametrize("language", ["ko", "en"])
def test_resume_prompt_asks_before_working(language):
    p = resume_prompt(language)
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_missing_output_prompt_says_not_to_look_for_the_old_code(language):
    # 이 지시가 없으면 에이전트가 삭제된 트리를 찾아 파일시스템을 훑는다
    # (실측: 19초 이상). 두 언어 모두 명시해야 한다.
    p = missing_output_prompt(language, spec_key=SPEC)
    assert SPEC in p
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_handoff_prompt_carries_the_summary(language):
    p = handoff_prompt(language, spec_key=SPEC,
                       summary="장바구니 화면을 만들었다", remaining="결제 연동")
    assert "장바구니 화면을 만들었다" in p
    assert "결제 연동" in p
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_tool_texts_exist_for_both_languages(language):
    assert "prototype/" in build_complete_description(language)
    assert build_complete_rejection(language).strip() != ""


def test_an_unknown_language_falls_back_to_korean():
    assert _plan("klingon") == _plan("ko")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_prompts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.proto.prompts'`

- [ ] **Step 3: `prompts.py`를 만든다 — 한국어 판은 현재 문장을 그대로 옮긴다**

`backend/pathfinder/proto/prompts.py`:

```python
# backend/pathfinder/proto/prompts.py — 빌드 에이전트의 개시 프롬프트, 언어별.
#
# **조립하지 않고 언어별로 완성된 문장 두 벌을 유지한다.** 이 프롬프트는 빌드
# 에이전트의 유일한 브레이크다(proto/session.py의 first_prompt docstring):
# 빌더는 bypassPermissions로 돌아 Write/Edit이 자동 승인되므로, "계획만 세우고
# 빌드하지 마"를 이 텍스트 밖에서 강제할 방법이 없다. 문장을 쪼개 치환하면 그
# 지시의 강도가 어느 언어에서 약해졌는지 알 수 없게 된다 — 그것이 두 벌을
# 유지하는 비용을 감수하는 이유다.
#
# 한국어 문장은 종전 session.py의 것을 그대로 옮긴 것이다(워크숍에서 검증된
# 문구를 다시 쓰지 않는다). 영어는 같은 지시를 같은 순서로 옮긴 것이고,
# test_proto_prompts가 두 벌에 같은 항목이 있는지 검사한다.
from __future__ import annotations

_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"


def _lang(language: str) -> str:
    return language if language in _LANGUAGES else _DEFAULT


def plan_prompt(language: str, *, spec_key: str, proxy_path: str) -> str:
    """처음부터 시작하는 세션의 개시 턴. 계획만 세우고 빌드하지 않는다."""
    if _lang(language) == "en":
        return (
            f"Read `{spec_key}` and draw up a plan for building this prototype.\n"
            "**In this turn, plan only — do not start building.**\n\n"
            "How to proceed:\n"
            f"1. First read `{spec_key}` and get the requirements exactly right.\n"
            "2. Then present your implementation plan. Include the tech stack, the "
            "list of screens and features you will build, the file structure, and "
            "the order of work; also state what was ambiguous in the spec and what "
            "you assumed on your own.\n"
            "3. After presenting the plan, **you MUST use AskUserQuestion to ask "
            "whether to execute it as written or change something, and wait for my "
            "answer.** Do not move on without approval.\n"
            "4. Do not create or modify any file during the planning stage "
            "(no Write/Edit). Touch nothing except reading the spec, and show the "
            "plan in the message body only.\n"
            "5. Start building only after I approve. While building, if anything is "
            "uncertain or needs a decision, do not decide it on your own — ask with "
            "AskUserQuestion first.\n\n"
            "Rules for the build stage (apply after approval):\n"
            "- Put the finished work under `prototype/` in the working directory, "
            "and write a README explaining how to build and run it.\n"
            f"- This prototype is served under a path proxy (e.g. `{proxy_path}`). "
            "Use basePath and relative paths so it works correctly no matter which "
            "sub-path it is placed under (never hardcode absolute paths).\n"
            "- If the code needs to call an LLM, use Amazon Bedrock through the "
            "default credential chain (instance/execution role). Do not hardcode an "
            "API key; read the region and model ID from environment variables.\n"
            "- **Read the model ID from `process.env.BEDROCK_MODEL_ID`** (or the "
            "equivalent for your language). Hosting injects the project's configured "
            "model under that name — a different name, or a specific model ID "
            "baked in as the default, means the model the user chose is ignored. If "
            "you need a fallback when the variable is absent, do not quietly use a "
            "hardcoded model; surface that the setting is missing.\n"
            "- **Write the prototype's own on-screen text in English** — labels, "
            "buttons, headings, placeholder copy, and any sample data a viewer "
            "reads. The prototype is a single-language demo; do not build an i18n "
            "layer into it.\n"
            "- When the prototype is finished, **declare completion with the "
            "`build_complete` tool.** Summarize what you built in `summary`, and put "
            "any remaining work or known limitations in `remaining`. The build "
            "session ends after this declaration, so if work is left, do not declare "
            "it — keep going.\n"
        )
    return (
        f"`{spec_key}` 파일을 읽고, 프로토타입 구현 계획을 세워줘.\n"
        "**이번 턴에서는 계획만 세우고 빌드는 시작하지 마.**\n\n"
        "진행 방식:\n"
        f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악해줘.\n"
        "2. 그다음 구현 계획을 제시해줘. 기술 스택, 만들 화면/기능 목록, "
        "파일 구조, 작업 순서를 포함하고, 스펙에서 애매했던 부분과 네가 임의로 "
        "가정한 내용도 함께 밝혀줘.\n"
        "3. 계획을 제시한 뒤 **반드시 AskUserQuestion으로 이 계획대로 실행할지, "
        "수정할 부분이 있는지 물어보고 내 답을 기다려줘.** 승인 없이 다음 단계로 "
        "넘어가면 안 돼.\n"
        "4. 계획 단계에서는 파일을 만들거나 수정하지 마(Write/Edit 금지). "
        "스펙을 읽는 것 외에는 아무것도 건드리지 말고, 계획은 메시지 본문으로만 "
        "보여줘.\n"
        "5. 내가 승인한 뒤에 빌드를 시작해줘. 빌드 중에도 불확실하거나 결정이 "
        "필요한 사항이 있으면 마음대로 넘기지 말고 AskUserQuestion으로 먼저 "
        "물어봐줘.\n\n"
        "빌드 단계에서 지킬 것(승인 후 적용):\n"
        "- 완성물은 반드시 작업 디렉토리 아래 `prototype/`에 두고, 빌드 방법과 "
        "실행 방법을 설명하는 README를 함께 작성해줘.\n"
        f"- 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
        "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
        "구현해줘(절대 경로 하드코딩 금지).\n"
        "- 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
        "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
        "환경변수로 받도록 구현해줘.\n"
        "- **모델 ID는 반드시 `process.env.BEDROCK_MODEL_ID`(또는 언어에 맞는 "
        "동등 표현)로 읽어줘.** 호스팅이 이 이름으로 프로젝트에 설정된 모델을 "
        "주입한다 — 다른 이름을 쓰거나 특정 모델 ID를 기본값으로 박아 두면 "
        "사용자가 고른 모델이 무시된다. 환경변수가 없을 때의 폴백이 필요하면 "
        "하드코딩한 모델로 조용히 넘어가지 말고 설정이 없다는 것을 드러내줘.\n"
        "- **프로토타입 화면의 문구는 한국어로 써줘** — 라벨, 버튼, 헤딩, "
        "플레이스홀더, 그리고 보는 사람이 읽는 샘플 데이터까지. 프로토타입은 "
        "단일 언어 데모이니 i18n 계층을 만들지는 마.\n"
        "- 프로토타입이 완성되면 **`build_complete` 도구로 완료를 선언해줘.** "
        "무엇을 만들었는지 요약(summary)과, 남은 작업이나 알려진 한계가 있으면 "
        "remaining에 적어줘. 이 선언 뒤 빌드 세션이 종료되니, 아직 작업이 "
        "남았으면 선언하지 말고 계속 진행해줘.\n"
    )


def resume_prompt(language: str) -> str:
    """죽은 세션을 이어받는 개시 턴.

    의도적으로 짧다. 에이전트는 이전 트랜스크립트와 만든 것을 이미 갖고 있어서,
    스펙이나 빌드 규칙을 다시 말하면 그가 이미 보는 것과 경쟁만 한다. 이 턴이
    할 일은 그가 혼자 방향을 정하지 않게 막는 것뿐이다.
    """
    if _lang(language) == "en":
        return (
            "Continuing the previous build session.\n"
            "**Do not build or modify anything yet.**\n\n"
            "1. Briefly summarize what has been done so far and what is left.\n"
            "2. Then **use AskUserQuestion to ask what to work on this time, and "
            "wait for my answer.** Offer options so I can choose between continuing "
            "the remaining work and doing something else first.\n"
            "3. Start working only after I choose.\n"
        )
    return (
        "이전 빌드 세션을 이어서 진행한다.\n"
        "**아직 아무것도 빌드하거나 수정하지 마.**\n\n"
        "1. 지금까지 진행한 내용과 남은 작업을 짧게 정리해줘.\n"
        "2. 그다음 **AskUserQuestion으로 이번에 무엇을 진행할지 물어보고 내 답을 "
        "기다려줘.** 남은 작업을 이어서 할지, 다른 것을 먼저 할지 내가 고를 수 "
        "있게 선택지를 제시해줘.\n"
        "3. 내가 고른 뒤에 작업을 시작해줘.\n"
    )


def missing_output_prompt(language: str, *, spec_key: str) -> str:
    """산출물이 사라진 뒤의 개시 턴 — 찾지 말고 다시 만들라고 말한다.

    이 지시가 없으면 에이전트는 트랜스크립트를 믿고 없는 코드를 찾아 나선다.
    실측: 리셋된 프로토타입에서 작업 디렉토리 → 다른 프로토타입 디렉토리 →
    `/opt/pathfinder/frontend` → 파일시스템 전체로 탐색을 넓히며 19초 이상을
    태웠고, 성공할 수 없는 탐색이었다.
    """
    if _lang(language) == "en":
        return (
            "The record of the previous build session is still here, but "
            "**there is no output under `prototype/`** in the working directory. "
            "It was reset, or the build environment was replaced.\n\n"
            "**Do not look for the old code.** It is nowhere in this environment. "
            f"Read `{spec_key}` again and **just build it from scratch.** Reuse the "
            "direction and the decisions from the earlier conversation.\n\n"
            "**Do not start building yet.**\n"
            "1. Read the spec and give me a short implementation plan that reflects "
            "what we agreed on earlier.\n"
            "2. Then **use AskUserQuestion to ask whether to rebuild it this way, "
            "and wait for my answer.**\n"
            "3. Start building only after I approve. Put the finished work under "
            "`prototype/` in the working directory, and declare completion with "
            "`build_complete` when you are done.\n"
        )
    return (
        "이전 빌드 세션의 기록은 남아 있지만, 작업 디렉토리의 "
        "`prototype/`에 **산출물이 없다.** 초기화됐거나 빌드 환경이 "
        "교체된 것이다.\n\n"
        "**이전 코드를 찾지 마.** 이 환경 어디에도 남아 있지 않다. "
        f"`{spec_key}`를 다시 읽고 **처음부터 다시 만들면 된다.** "
        "이전 대화에서 정한 방향과 결정사항은 그대로 활용해줘.\n\n"
        "**아직 빌드는 시작하지 마.**\n"
        "1. 스펙을 읽고, 이전 대화에서 합의된 내용을 반영한 구현 계획을 "
        "짧게 제시해줘.\n"
        "2. 그다음 **AskUserQuestion으로 이 계획대로 다시 만들지 물어보고 내 "
        "답을 기다려줘.**\n"
        "3. 내가 승인한 뒤에 빌드를 시작해줘. 완성물은 작업 디렉토리 아래 "
        "`prototype/`에 두고, 끝나면 `build_complete`로 완료를 선언해줘.\n"
    )


def handoff_prompt(language: str, *, spec_key: str, summary: str,
                   remaining: str) -> str:
    """완료된 빌드를 개선하는 새 세션의 개시 턴.

    파일 트리를 넘기지 않는 것이 의도적이다 — 에이전트가 자기 파일 도구로 cwd를
    읽는 편이 스냅샷보다 정확하다. 여기서 할 일은 이전 빌드가 무엇을 남겼는지
    알려주고 마음대로 손대지 않게 막는 것뿐이다.
    """
    if _lang(language) == "en":
        return (
            "This prototype has already been built once. This session is for "
            "improvements.\n\n"
            f"Summary of the previous build:\n{summary}\n\n"
            f"Recorded as remaining work:\n{remaining}\n\n"
            "**Do not modify anything yet.**\n"
            "1. First look at `prototype/` in the working directory to see where "
            f"things stand. Re-read `{spec_key}` if you need to.\n"
            "2. Then **use AskUserQuestion to ask what to improve this time, and "
            "wait for my answer.** Offer options so I can choose between the "
            "remaining work recorded above and something else.\n"
            "3. Start working only after I choose. When the improvements are done, "
            "declare completion with `build_complete` again.\n"
        )
    return (
        "이 프로토타입은 이미 한 번 빌드가 완료됐다. 이번 세션은 개선 "
        "작업이다.\n\n"
        f"이전 빌드 요약:\n{summary}\n\n"
        f"남은 작업으로 기록된 것:\n{remaining}\n\n"
        "**아직 아무것도 수정하지 마.**\n"
        f"1. 먼저 작업 디렉토리의 `prototype/`을 살펴보고 현재 상태를 파악해줘. "
        f"필요하면 `{spec_key}`도 다시 읽어줘.\n"
        "2. 그다음 **AskUserQuestion으로 이번에 무엇을 개선할지 물어보고 내 "
        "답을 기다려줘.** 위에 기록된 남은 작업을 할지, 다른 것을 할지 내가 "
        "고를 수 있게 선택지를 제시해줘.\n"
        "3. 내가 고른 뒤에 작업을 시작해줘. 개선이 끝나면 다시 "
        "`build_complete`로 완료를 선언해줘.\n"
    )


def build_complete_description(language: str) -> str:
    """`build_complete` 도구 설명. 도구 설명은 모델이 읽는 프롬프트다."""
    if _lang(language) == "en":
        return ("Declare that the prototype build is complete. Call this only "
                "**after you have produced real output under `prototype/`** — an "
                "empty directory means the declaration is rejected. The build "
                "session ends after this declaration, so do not call it while work "
                "remains.")
    return ("프로토타입 빌드가 완료되었음을 선언한다. **prototype/ 아래에 실제 "
            "산출물을 만든 뒤** 호출해야 한다 — 비어 있으면 선언이 거부된다. "
            "이 선언 뒤 빌드 세션이 종료되므로, 아직 작업이 남았으면 호출하지 마라.")


def build_complete_rejection(language: str) -> str:
    """산출물 없이 완료를 선언했을 때 모델에게 돌려주는 거부 메시지."""
    if _lang(language) == "en":
        return ("Rejected — there is no output under `prototype/` in the working "
                "directory. Build the prototype there first, then declare "
                "completion.")
    return ("거부됨 — 작업 디렉토리의 `prototype/` 아래에 산출물이 없다. "
            "먼저 그곳에 프로토타입을 만든 뒤 완료를 선언하라.")


def missing_remaining_note(language: str) -> str:
    """handoff에서 남은 작업 기록이 없을 때 쓰는 자리표시."""
    return ("(nothing recorded)" if _lang(language) == "en"
            else "(따로 기록된 것 없음)")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_prompts.py -q`
Expected: PASS (16 tests)

- [ ] **Step 5: `session.py`가 `prompts.py`에 위임하게 한다**

`PrototypeSession.__init__`에 파라미터를 추가한다(`idle_seconds` 앞):

```python
        semaphore: SemaphoreLike,
        language: str = "ko",
        idle_seconds: int | float = 1800,
    ):
```

본문에 저장:

```python
        # 이 프로젝트의 생성물 언어. 개시 프롬프트와 build_complete 도구
        # 텍스트를 이 값으로 고른다(proto/prompts.py).
        self._language = language
```

`first_prompt` 이하 프롬프트 메서드 4개(`_plan_prompt`, `_resume_prompt`, `_missing_output_prompt`, `_handoff_prompt`)의 **본문을 위임으로 교체한다.** docstring은 남긴다 — 왜 이 프롬프트가 이렇게 생겼는지의 기록이 코드에서 사라지면 안 된다. 대신 각 docstring 끝에 한 줄을 더한다:

```python
        문장 자체는 proto/prompts.py가 언어별로 갖고 있다.
```

교체된 본문:

```python
    def _plan_prompt(self) -> str:
        return prompts.plan_prompt(
            self._language,
            spec_key=self._spec_key(),
            proxy_path=f"/api/proto/{self.project_id}/{self.slug}/")

    def _resume_prompt(self) -> str:
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.resume_prompt(self._language)

    def _missing_output_prompt(self) -> str:
        return prompts.missing_output_prompt(self._language,
                                             spec_key=self._spec_key())

    def _handoff_prompt(self, handoff: dict) -> str:
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.handoff_prompt(
            self._language,
            spec_key=self._spec_key(),
            summary=handoff["summary"],
            remaining=handoff.get("remaining")
            or prompts.missing_remaining_note(self._language))
```

임포트를 추가: `from pathfinder.proto import prompts`

`send_message`의 완료된 세션 거부 문구(`"이 빌드 세션은 이미 완료되어..."`)도 언어별로 만든다. `prompts.py`에 추가:

```python
def session_already_complete(language: str) -> str:
    """완료 선언 뒤 메시지를 받았을 때 사용자에게 보이는 안내."""
    if _lang(language) == "en":
        return ("This build session is already complete and cannot take more "
                "messages. To keep improving, start a new session with "
                "\"Continue improving\".")
    return ("이 빌드 세션은 이미 완료되어 더 이상 메시지를 받을 수 "
            "없습니다. 개선 작업이 필요하면 '개선 이어서 하기'로 "
            "새 세션을 시작해 주세요.")
```

`session.py`의 해당 자리에서 `prompts.session_already_complete(self._language)`를 쓴다.

- [ ] **Step 6: `proto/tools.py`를 고친다**

`build_proto_tools`가 언어를 받게 하고, 파일 상단 임포트에 `from pathfinder.proto import prompts`를 추가한 뒤 함수 본문의 세 문자열을 교체한다. **`emit` 뒤의 `"빌드 완료가 기록되었다. 세션을 종료한다."`도 모델이 읽는 도구 결과이므로 함께 언어별로 만든다:**

```python
def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None],
                      language: str = "ko") -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 SdkMcpTool 리스트.

    Discovery의 build_tools와 같은 계약이다 — 이 리스트 자체는
    ClaudeAgentOptions에 바로 넣을 수 없고, 호출부(proto/builder.py)가
    create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME, tools=...)로 감싼다.

    language는 도구 설명과 반환 문자열의 언어다 — 셋 다 모델이 읽는
    프롬프트이므로 대화 언어와 맞아야 한다(proto/prompts.py).
    """

    @tool("build_complete",
          prompts.build_complete_description(language),
          _BUILD_COMPLETE_SCHEMA)
    async def build_complete(args: dict[str, Any]) -> dict[str, Any]:
        summary = args["summary"]
        remaining = args.get("remaining", "")

        # 이 이벤트가 세션을 끝낸다. 산출물 없이 선언되면 사용자는 "빌드
        # 완료" 카드를 보는데 호스팅할 것이 없다 — submit_document가 파일
        # 존재를 확인하는 것과 같은 이유로 여기서 막는다. 반환 문자열은
        # 에이전트가 읽고 스스로 고칠 수 있도록 무엇을 해야 하는지 알려준다.
        if not _has_output(workspace):
            _log.warning("build_complete refused: prototype/ is empty (%s)",
                         workspace)
            return _text_result(prompts.build_complete_rejection(language))

        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
        return _text_result(prompts.build_complete_recorded(language))

    return [build_complete]
```

`prompts.py`에 세 번째 함수를 추가한다(Step 3의 `build_complete_rejection` 뒤):

```python
def build_complete_recorded(language: str) -> str:
    """완료 선언을 받아들였을 때 모델에게 돌려주는 확인."""
    if _lang(language) == "en":
        return "Build completion recorded. Ending the session."
    return "빌드 완료가 기록되었다. 세션을 종료한다."
```

그리고 `build_complete_rejection`의 한국어 문장은 **종전 `tools.py`의 것을 그대로** 쓴다(Step 3에서 적은 것을 아래로 교체):

```python
def build_complete_rejection(language: str) -> str:
    """산출물 없이 완료를 선언했을 때 모델에게 돌려주는 거부 메시지."""
    if _lang(language) == "en":
        return ("Rejected — there is no output under `prototype/` in the working "
                "directory. Write the finished work to `prototype/` and declare "
                "completion again.")
    return ("거부됨 — 작업 디렉토리의 `prototype/` 아래에 산출물이 없다. "
            "완성물을 `prototype/`에 쓴 뒤 다시 선언해라.")
```

`test_proto_prompts.py`에 이 함수의 단정을 더한다:

```python
@pytest.mark.parametrize("language", ["ko", "en"])
def test_build_complete_recorded_exists(language):
    from pathfinder.proto.prompts import build_complete_recorded
    assert build_complete_recorded(language).strip() != ""
```

`proto/builder.py`의 `_proto_tools_for`에서 빌더의 언어를 넘긴다. `PrototypeBuilder.__init__`에 `language: str = "ko"`를 추가하고 `self._language = language`로 저장한 뒤:

```python
    return build_proto_tools(builder._workspace, builder._queue.append,
                            builder._language)
```

- [ ] **Step 7: `app.py`의 두 팩토리를 고친다**

`proto_session_factory`(308-337행):

```python
    language = project_language(project_id)

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            config_dir=str(config_dir),
            session_id=session_id,
            resume=resume,
            session_store=store,
            anthropic_model=project_model(project_id),
            language=language,
            permission_mode=_proto_permission_mode(),
        )

    return PrototypeSession(
        project_id=project_id, slug=slug, s3=s3,
        build_root=build_root,
        builder_factory=builder_factory,
        semaphore=build_semaphore,
        language=language,
    )
```

- [ ] **Step 8: 기존 테스트를 고친다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py tests/test_proto_tools.py tests/test_proto_builder.py -q 2>&1 | tail -15`

`first_prompt()`의 한국어 문구를 단정하는 테스트(685-880행 부근)는 기본 언어가 `ko`이므로 대부분 통과한다. 실패하는 것은 **문장이 미세하게 달라진 곳**뿐이다 — `_missing_output_prompt`의 마지막 문장을 위 `prompts.py`에서 두 문장으로 나눠 적었으므로, 그 문구를 단정하는 테스트가 있으면 새 문장으로 고친다.

영어 세션 테스트를 하나 추가한다:

```python
def test_first_prompt_is_english_for_an_english_project(tmp_path):
    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=FakeS3Store(),
        build_root=tmp_path / "protos",
        builder_factory=lambda sid, resume: FakeBuilder(),
        semaphore=BuildSemaphore(max_concurrent=2),
        language="en",
    )
    prompt = session.first_prompt()
    assert "do not start building" in prompt.lower()
    assert "AskUserQuestion" in prompt
    assert not any("가" <= c <= "힣" for c in prompt)
```

- [ ] **Step 9: 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: 전부 통과

- [ ] **Step 10: 수동 검증**

영어 프로젝트에서 프로토타입을 하나 빌드한다:

1. Task 10의 수동 검증에서 만든 영어 프로젝트로 Discovery를 진행해 `PROTOTYPE-*.md` 스펙을 만든다.
2. `프로토타입` 탭에서 빌드를 시작한다.
3. 확인 항목:
   - 개시 턴의 대화가 영어인가
   - **계획만 세우고 멈췄는가** — 이것이 프롬프트의 유일한 브레이크다. 승인 없이 빌드가 시작되면 영어 프롬프트의 지시가 약한 것이다.
   - `AskUserQuestion` 카드가 떴는가
   - 승인 후 산출물이 `prototype/` 아래에 생기는가
   - 완료 시 `build_complete`가 호출되는가
   - **생성된 앱의 화면 문구가 영어인가**

빌드가 승인 없이 시작되면 `plan_prompt`의 영어 판에서 금지 문장을 강화한다 — 한국어 판이 `**...마.**`로 강조하는 것과 같은 무게가 되도록.

- [ ] **Step 11: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/proto/ backend/pathfinder/app.py backend/tests/
git commit -m "feat(proto): 빌드 프롬프트를 언어별 두 벌로

조립하지 않고 완성된 문장 두 벌을 유지한다 — 이 프롬프트가 빌드 에이전트의
유일한 브레이크이고(bypassPermissions로 돌아 Write/Edit이 자동 승인된다),
문장을 쪼개면 '계획만 세우고 빌드하지 마'의 강도가 어느 언어에서 약해졌는지
알 수 없다.

한국어 문장은 종전 session.py의 것을 그대로 옮겼다 — 워크숍에서 검증된 문구를
다시 쓰지 않는다. test_proto_prompts가 두 벌에 같은 지시가 있는지 검사한다.

수동 검증: 영어 프로젝트에서 빌드를 돌려 계획 단계에서 멈추는지 확인했다."
```

---

## Task 12: 설문 — 문항 생성 프롬프트와 리포트 라벨

**Files:**
- Create: `backend/pathfinder/survey/report_labels.py`
- Modify: `backend/pathfinder/survey/builder.py`
- Modify: `backend/pathfinder/survey/store.py:111-230`
- Modify: `backend/pathfinder/survey/models.py` (`Questionnaire.language`)
- Modify: `backend/pathfinder/app.py:352-356` (`survey_store_factory`)
- Modify: `backend/pathfinder/routes/surveys.py:65-67`
- Modify: `frontend/app/survey/[token]/page.tsx`, `frontend/components/survey/SurveyForm.tsx`
- Modify: `frontend/lib/api/surveys.ts` (`PublicSurvey.language`)
- Test: `backend/tests/test_survey_builder.py`, `backend/tests/test_survey_store.py`, `backend/tests/test_routes_surveys.py`

**Interfaces:**
- Consumes: `app_module.project_language(pid)` (Task 3), `LocaleProvider` (Task 1)
- Produces:
  - `build_prompt(prototype_md: str, language: str = "ko") -> str`
  - `build_questionnaire(prototype_md, agent, *, token, project_id, slug, now, language="ko", attempts=2) -> Questionnaire`
  - `Questionnaire.language: str = "ko"`
  - `SurveyStore(project_s3, root_s3, slug, project_id, language="ko")`
  - `GET /public/surveys/{token}` 응답에 `"language"`

**공개 설문 페이지는 UI 쿠키가 아니라 설문 언어를 따른다.** 응답자는 외부인이라 `pf_lang` 쿠키가 없고, 문항이 영어인데 화면만 한국어인 것은 응답자에게 더 나쁘다(스펙 §범위 밖의 그 판단).

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 문항 생성 프롬프트**

`backend/tests/test_survey_builder.py`에 추가:

```python
def test_prompt_is_korean_by_default():
    from pathfinder.survey.builder import build_prompt
    p = build_prompt("# PROTOTYPE-demo")
    assert any("가" <= c <= "힣" for c in p)


def test_prompt_is_english_for_an_english_project():
    from pathfinder.survey.builder import build_prompt
    p = build_prompt("# PROTOTYPE-demo", language="en")
    # 프로토타입 명세가 프롬프트에 실리므로 명세의 글자는 제외하고 본다.
    body = p.replace("# PROTOTYPE-demo", "")
    assert not any("가" <= c <= "힣" for c in body), body[:400]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_keeps_every_requirement(language):
    """두 언어가 같은 제약을 담아야 한다. 하나라도 빠지면 그 언어의 설문이
    프로토타입으로 답할 수 없는 것을 묻거나(성능·보안), 집계가 신호와 잡음을
    구별할 수 없게 된다(해당 없음 선택지)."""
    from pathfinder.survey.builder import build_prompt
    p = build_prompt("# spec", language=language)
    assert "scale" in p and "choice" in p and "text" in p     # 문항 타입 3종
    assert "JSON" in p
    assert "hypothesis" in p and "questions" in p             # 출력 스키마


@pytest.mark.parametrize("language", ["ko", "en"])
async def test_build_questionnaire_records_the_language(language):
    from pathfinder.survey.builder import build_questionnaire

    async def agent(prompt):
        return ('{"title": "T", "hypothesis": "H", "questions": '
                '[{"id": "q1", "text": "Q", "type": "text", "required": false}]}')

    qn = await build_questionnaire("# spec", agent, token="tok",
                                   project_id="p1", slug="demo",
                                   now="2026-08-03T00:00:00+00:00",
                                   language=language)
    # 언어를 questionnaire에 기록해야 공개 응답 페이지가 그 언어로 그릴 수 있다.
    assert qn.language == language
```

기존 테스트가 `pytest.mark.asyncio`를 쓰는 방식을 따른다: `cd backend && head -20 tests/test_survey_builder.py`

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_builder.py -q 2>&1 | tail -8`
Expected: FAIL — `build_prompt() got an unexpected keyword argument 'language'`

- [ ] **Step 3: `builder.py`를 고친다**

기존 `QUESTIONNAIRE_PROMPT`를 `QUESTIONNAIRE_PROMPT_KO`로 이름을 바꾸고(내용은 그대로), 영어 판을 추가한다:

```python
# 영어 판. 한국어 판과 **같은 제약을 같은 순서로** 담는다 — 조립하지 않고 두
# 벌을 유지하는 이유는 proto/prompts.py와 같다(제약이 하나 빠지면 그 언어의
# 설문만 조용히 나빠진다). test_survey_builder가 두 벌의 대조를 지킨다.
QUESTIONNAIRE_PROMPT_EN = """\
Below is a prototype spec (PROTOTYPE-*.md). Write validation survey questions to
ask someone who **has tried this prototype**.

What the respondent saw is the premise of every question. **This is a validation
prototype, not a finished product** — a demo where only the core flow works, the
data may be mocked, and some features may be screens only. It is not production
code; security, error handling, and scalability were deliberately left out
(prototype-validation.md Step 3).

So do not ask about any of the following — a prototype cannot answer them, and an
answer would only penalize what was built that way on purpose:
- performance, response time, stability (errors, downtime)
- security, permissions, handling of personal data
- accuracy of real data (nobody can judge that from mock data)
- purchase decisions such as timing, pricing, or contracts
- production operations, maintenance, or completeness of integrations

Ask instead about what the prototype really can answer: whether the problem was
identified correctly, whether the proposed approach is a direction that solves it,
whether the flow is understandable, and what is missing. Phrase questions
**hypothetically** ("if this approach were adopted in your actual work…") so the
respondent evaluates the approach rather than the polish of the demo.

Requirements:
- 6 to 10 questions.
- Include questions that produce evidence for judging whether the spec's
  validation hypothesis and success criteria hold.
- Include a question asking whether each major feature is a **direction that
  solves** the user's problem.
- Include at least one free-response question that surfaces improvements and
  missing needs.
- For choice questions about a specific feature, include an option like **"did
  not use it / not applicable"**. Not reaching some features in a prototype is
  normal (the rule's Feature Validation table has a "Not tested — Users did not
  reach this feature" row), and without that option respondents guess about
  features they never tried, which leaves the aggregate unable to tell signal
  from noise.
- Do not write leading questions (questions that hint at the answer you want).

Use exactly these three question types:
- "scale": a 1-5 scale. Do not include options.
- "choice": single select. Include two or more options.
- "text": free response. Do not include options.

Output **only** one JSON object in the shape below (no explanation, no preamble,
no code fence):
{{"title": "...", "hypothesis": "...", "questions": [
  {{"id": "q1", "text": "...", "type": "scale", "required": true}},
  {{"id": "q2", "text": "...", "type": "choice", "options": ["...", "..."], "required": true}},
  {{"id": "q3", "text": "...", "type": "text", "required": false}}
]}}

Spec:
---
{md}
---
"""

_PROMPTS = {"ko": QUESTIONNAIRE_PROMPT_KO, "en": QUESTIONNAIRE_PROMPT_EN}


def build_prompt(prototype_md: str, language: str = "ko") -> str:
    """설문 문항 생성 프롬프트. 알 수 없는 언어는 한국어로 떨어진다."""
    template = _PROMPTS.get(language, QUESTIONNAIRE_PROMPT_KO)
    return template.format(md=prototype_md)
```

`build_questionnaire`에 파라미터를 추가하고 `Questionnaire`에 실어 준다:

```python
async def build_questionnaire(prototype_md: str, agent, *, token: str,
                              project_id: str, slug: str, now: str,
                              language: str = "ko",
                              attempts: int = 2) -> Questionnaire:
    prompt = build_prompt(prototype_md, language)
    last_error: Exception | None = None
    for attempt in range(attempts):
        reply = await agent(prompt)
        try:
            data = _extract_json(reply)
            return Questionnaire(
                token=token, status="open", slug=slug, project_id=project_id,
                created_at=now, closed_at=None,
                # 문항의 언어를 기록한다 — 공개 응답 페이지가 이 값으로 화면을
                # 그린다. 응답자는 외부인이라 pf_lang 쿠키가 없고, 문항이
                # 영어인데 화면만 한국어인 것은 응답자에게 더 나쁘다.
                language=language,
                title=data["title"], hypothesis=data["hypothesis"],
                questions=data["questions"])
        except Exception as exc:  # noqa: BLE001 — retry on any malformed reply
            last_error = exc
            _log.warning("questionnaire generation attempt %d failed: %s",
                         attempt + 1, exc)
    raise ValueError(f"questionnaire generation failed: {last_error}")
```

`survey/models.py`의 `Questionnaire`에 필드를 추가:

```python
    # 문항이 쓰인 언어("ko"|"en"). 공개 응답 페이지가 이 값으로 화면 문구를
    # 고른다. 기존 설문에는 없어 기본값이 필요하다 — 그 설문들은 모두
    # 한국어로 만들어졌다.
    language: str = "ko"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_builder.py -q`
Expected: PASS

- [ ] **Step 5: 실패하는 테스트를 쓴다 — 리포트 라벨**

`backend/tests/test_survey_store.py`에 추가:

```python
@pytest.mark.asyncio
async def test_results_markdown_is_english_for_an_english_survey(tmp_path):
    """리포트는 aiplc-docs/**에 생성되는 산출물이므로 UI 언어가 아니라
    프로젝트 언어를 따른다."""
    from pathfinder.survey.store import _results_markdown
    from pathfinder.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token="t", status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None, language="en",
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, updated_at="2026-08-03T00:00:00+00:00")
    md = _results_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "en")
    assert "Prototype" in md or "prototype" in md
    assert not any("가" <= c <= "힣" for c in md), md[:400]


@pytest.mark.asyncio
async def test_results_markdown_stays_korean_by_default(tmp_path):
    from pathfinder.survey.store import _results_markdown
    from pathfinder.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token="t", status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None,
        title="T", hypothesis="H", questions=[])
    rollup = Rollup(count=0, per_question={}, updated_at="2026-08-03T00:00:00+00:00")
    md = _results_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "ko")
    assert "프로토타입" in md
```

`Rollup`/`Questionnaire`의 실제 필수 필드는 기존 테스트를 따른다: `cd backend && grep -n "Rollup(" tests/test_survey_store.py | head -3`

- [ ] **Step 6: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store.py -q -k markdown 2>&1 | tail -6`
Expected: FAIL — `_results_markdown() takes 4 positional arguments but 5 were given`

- [ ] **Step 7: `report_labels.py`를 만들고 `store.py`를 고친다**

`backend/pathfinder/survey/report_labels.py`:

```python
# backend/pathfinder/survey/report_labels.py — 설문 리포트 마크다운의 라벨.
#
# 이것은 UI 문구가 아니라 **문서 생성기**다. 리포트는 aiplc-docs/** 아래에
# 산출물로 저장되고 문서 리뷰 화면과 개발자 핸드오프에 들어가므로, UI 언어
# (사용자별 쿠키)가 아니라 프로젝트 언어를 따른다.
#
# error_codes.py의 "백엔드에 번역 시스템을 만들지 않는다"와 모순되지 않는다:
# 그쪽은 사용자에게 보이는 에러 문구이고 UI 언어를 백엔드가 모른다. 여기는
# 문서이고 프로젝트 언어를 백엔드가 이미 안다.
#
# 영어를 그대로 두는 헤딩이 있다: prototype-validation.md Step 6이 정한 섹션
# 이름(`## Feedback Sources`, `## Theme Analysis`, `## Pain Point Mapping`,
# `## Build Decision`)과 표 헤더는 **양쪽 언어에서 영어다.** 룰이 그 이름으로
# 문서를 찾고, 상류 워크플로우가 그 구조를 전제하기 때문이다.
from __future__ import annotations

_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"

_LABELS = {
    "ko": {
        "prototype": "프로토타입",
        "survey": "설문",
        "hypothesis": "검증 가설",
        "response_count": "응답 수",
        "survey_status": "설문 상태",
        "collected_at": "취합 시각",
        "status_closed": "마감",
        "status_open": "진행 중",
        "source_name": "Pathfinder 검증 설문",
        "note": ("> 이 파일은 Pathfinder 설문 집계로 생성되었다. 아래 '정량 집계'와\n"
                 "> '자유 응답 전문'은 수집된 데이터이며, 테마 분석·pain point 매핑·\n"
                 "> 빌드 결정은 PM이 판단해 채운다(prototype-validation.md Step 6)."),
        "quantitative": "정량 집계",
        "free_text": "자유 응답 전문",
        "mean": "평균",
        "of_5": "/ 5",
        "responses_n": "응답 {n}건",
        "score": "점수",
        "count": "응답 수",
        "option": "선택지",
        "ratio": "비율",
        "free_n": "자유 응답 {n}건 — 전문은 아래 '자유 응답 전문' 참조",
        "no_response": "(응답 없음)",
        "theme_placeholder": "(PM이 위 자유 응답에서 도출)",
        "pain_placeholder": "(Envision의 pain point를 옮겨 판정)",
        "decision_proceed": "Proceed — 검증됨, 다음 단계로",
        "decision_iterate": "Iterate — 부분 검증, 프로토타입 수정 후 재검증",
        "decision_pivot": "Pivot — 접근 재고 (Envision으로 복귀)",
        "optional_suffix": " (선택)",
    },
    "en": {
        "prototype": "Prototype",
        "survey": "Survey",
        "hypothesis": "Validation hypothesis",
        "response_count": "Responses",
        "survey_status": "Survey status",
        "collected_at": "Aggregated at",
        "status_closed": "closed",
        "status_open": "open",
        "source_name": "Pathfinder validation survey",
        "note": ("> This file was generated by Pathfinder's survey aggregation. The\n"
                 "> quantitative summary and full free responses below are collected\n"
                 "> data; theme analysis, pain point mapping, and the build decision\n"
                 "> are for the PM to judge and fill in (prototype-validation.md\n"
                 "> Step 6)."),
        "quantitative": "Quantitative summary",
        "free_text": "Full free responses",
        "mean": "Mean",
        "of_5": "/ 5",
        "responses_n": "{n} responses",
        "score": "Score",
        "count": "Responses",
        "option": "Option",
        "ratio": "Share",
        "free_n": "{n} free responses — see 'Full free responses' below",
        "no_response": "(no responses)",
        "theme_placeholder": "(PM derives from the free responses above)",
        "pain_placeholder": "(carry over Envision's pain points and judge them)",
        "decision_proceed": "Proceed — validated, move on",
        "decision_iterate": "Iterate — partly validated, revise the prototype and retest",
        "decision_pivot": "Pivot — reconsider the approach (back to Envision)",
        "optional_suffix": " (optional)",
    },
}


def labels(language: str) -> dict[str, str]:
    """리포트 라벨 사전. 알 수 없는 언어는 한국어로 떨어진다."""
    return _LABELS[language if language in _LANGUAGES else _DEFAULT]
```

`store.py`의 `_results_markdown`과 `_to_markdown`에 `language` 파라미터를 추가하고, 한국어 리터럴을 `L["key"]`로 교체한다:

```python
def _results_markdown(qn: Questionnaire, responses: list, rollup: Rollup,
                      now: str, language: str = "ko") -> str:
    """Render the survey aggregate under prototype-validation.md's Step 6
    headings. Sections the rule expects the PM to judge (theme analysis, pain
    point mapping, build decision) are emitted as empty templates rather than
    machine guesses.

    Step 6이 정한 섹션 이름과 표 헤더는 양쪽 언어에서 영어다 — 룰이 그 이름으로
    문서를 찾는다(report_labels.py 헤더 참조).
    """
    from pathfinder.survey.report_labels import labels
    L = labels(language)
    lines = [
        "# Validation Results",
        "",
        f"- **{L['prototype']}**: {qn.slug}",
        f"- **{L['survey']}**: {qn.title}",
        f"- **{L['hypothesis']}**: {qn.hypothesis}",
        f"- **{L['response_count']}**: {rollup.count}",
        f"- **{L['survey_status']}**: "
        f"{L['status_closed'] if qn.status == 'closed' else L['status_open']}",
        f"- **{L['collected_at']}**: {now}",
        "",
        L["note"],
        "",
        "## Feedback Sources",
        "",
        "| Source | Type | Users | Feedback Items |",
        "|---|---|---|---|",
        f"| {L['source_name']} | Survey | {rollup.count} | {rollup.count} |",
        "",
        f"## {L['quantitative']}",
        "",
    ]
```

이후 본문의 한국어를 같은 방식으로 교체한다 — `평균 **{mean}** / 5 (응답 {n}건)`은 `f"{L['mean']} **{stat.mean}** {L['of_5']} ({L['responses_n'].format(n=stat.n)})"`가 된다. `_to_markdown`도 같은 방식으로 고친다(`검증 가설`, `(선택)`).

`SurveyStore.__init__`에 `language`를 받고 두 호출부에 넘긴다:

```python
    def __init__(self, project_s3, root_s3, slug: str, project_id: str,
                 language: str = "ko"):
        self._s3 = project_s3
        self._root = root_s3
        self.slug = slug
        self.project_id = project_id
        # 리포트 생성 언어. questionnaire.language가 아니라 프로젝트 언어를
        # 쓰는 이유: 리포트는 산출물 문서이고 문서 언어는 프로젝트가 정한다.
        # 정상 경로에서는 두 값이 같다(설문도 프로젝트 언어로 생성된다).
        self._language = language
```

`synthesize_results`의 `_results_markdown(...)` 호출에 `self._language`를 넘기고, `_to_markdown(qn)` 호출에도 넘긴다.

- [ ] **Step 8: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store.py -q`
Expected: PASS

- [ ] **Step 9: `app.py`와 라우트를 고친다**

`survey_store_factory`:

```python
def survey_store_factory(project_id: str, slug: str):
    from pathfinder.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id,
                       language=project_language(project_id))
```

`routes/surveys.py:65-67`의 `build_questionnaire` 호출에 언어를 추가:

```python
        qn = await build_questionnaire(
            prototype_md, app_module.questionnaire_agent_factory(pid),
            token=token, project_id=pid, slug=slug, now=_now(),
            language=app_module.project_language(pid))
```

`routes/surveys_public.py`의 공개 조회 응답에 `language`를 추가한다. 그 라우트가 `Questionnaire`를 어떤 모양으로 축약해 내보내는지 확인하고 필드를 하나 더한다:

Run: `cd backend && grep -n "async def get_public_survey" -A 25 pathfinder/routes/surveys_public.py`

응답 dict에 `"language": qn.language`를 추가한다. **`project_id`/`slug`/`token`은 계속 노출하지 않는다** — 그 파일 헤더의 규율이다.

- [ ] **Step 10: 프론트의 공개 설문 페이지를 고친다**

`frontend/lib/api/surveys.ts`의 `PublicSurvey`에 추가:

```typescript
  // 문항이 쓰인 언어. 응답 화면이 이 값으로 그려진다 — 응답자는 외부인이라
  // pf_lang 쿠키가 없고, 문항이 영어인데 화면만 한국어인 것은 더 나쁘다.
  language?: "ko" | "en";
```

`frontend/app/survey/[token]/page.tsx`에서 설문 언어로 `LocaleProvider`를 씌운다:

```typescript
import { LocaleProvider } from "@/lib/i18n/provider";
import { DEFAULT_LOCALE, isLocale } from "@/lib/i18n";

// ...
  // 이 페이지만 UI 쿠키를 무시하고 설문 언어를 쓴다. 응답자는 외부인이라
  // 쿠키가 없고(layout의 Provider는 ko가 된다), 문항이 영어인데 라벨만
  // 한국어인 화면은 응답자에게 더 나쁘다.
  const surveyLocale =
    state.kind === "ready" && isLocale(state.survey.language)
      ? state.survey.language
      : DEFAULT_LOCALE;

  return (
    <LocaleProvider locale={surveyLocale}>
      {/* 기존 렌더 트리 전체를 이 안으로 옮긴다 */}
    </LocaleProvider>
  );
```

`SurveyForm.tsx`와 이 페이지의 문구는 Task 9 배치 F/G에서 이미 `useT()`로 바뀌어 있으므로, 이 Provider가 그 값을 갈아끼운다.

`frontend/app/survey/[token]/page.test.tsx`에 테스트를 추가:

```typescript
it("영어 설문은 영어로 그려진다", async () => {
  server.use(
    http.get(`${API_BASE_URL}/public/surveys/tok`, () =>
      HttpResponse.json({ title: "T", hypothesis: "H", status: "open",
                          language: "en", questions: [] })),
  );
  render(<SurveyPage params={Promise.resolve({ token: "tok" })} />);
  // 제출 버튼 등 화면 문구가 영어인지 확인한다. 정확한 키는 배치 F/G에서
  // 정해진 것을 쓴다.
  expect(await screen.findByRole("button", { name: /Submit/i })).toBeInTheDocument();
});

it("언어를 모르는 설문(구 데이터)은 한국어로 그려진다", async () => {
  server.use(
    http.get(`${API_BASE_URL}/public/surveys/tok`, () =>
      HttpResponse.json({ title: "T", hypothesis: "H", status: "open",
                          questions: [] })),
  );
  render(<SurveyPage params={Promise.resolve({ token: "tok" })} />);
  expect(await screen.findByRole("button", { name: /제출/ })).toBeInTheDocument();
});
```

- [ ] **Step 11: 양쪽 전체 + 빌드**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -4`
Run: `cd frontend && npm run build 2>&1 | tail -6`
Expected: 전부 통과

- [ ] **Step 12: 수동 검증**

영어 프로젝트에서 설문을 만들고 응답한다:

1. Task 11에서 빌드한 영어 프로토타입의 `프로토타입` 탭 → 검증 설문 생성
2. 확인 항목:
   - **생성된 문항이 영어인가**
   - 공개 링크(`/survey/{token}`)를 **시크릿 창**에서 열었을 때 화면 문구가 영어인가 (쿠키가 없는 응답자 상황)
   - 응답을 제출하고 집계 후 `aiplc-docs/discovery/prototypes/{slug}/`의 리포트 문서가 영어인가
   - `## Feedback Sources`, `## Theme Analysis`, `## Pain Point Mapping`, `## Build Decision` 헤딩은 **영어로 남아 있는가**(양쪽 언어에서 영어여야 한다 — 룰이 그 이름으로 찾는다)
3. 한국어 프로젝트에서도 같은 흐름을 돌려 회귀가 없는지 확인한다.

- [ ] **Step 13: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/survey/ backend/pathfinder/app.py \
        backend/pathfinder/routes/surveys.py backend/pathfinder/routes/surveys_public.py \
        backend/tests/ frontend/lib/api/surveys.ts 'frontend/app/survey/'
git commit -m "feat(survey): 문항 생성 프롬프트와 리포트를 프로젝트 언어로

공개 응답 페이지는 UI 쿠키가 아니라 설문 언어를 따른다 — 응답자는 외부인이라
쿠키가 없고, 문항이 영어인데 라벨만 한국어인 화면은 더 나쁘다.

리포트의 Step 6 섹션 헤딩(Feedback Sources, Theme Analysis, Pain Point
Mapping, Build Decision)과 표 헤더는 양쪽 언어에서 영어로 남긴다 — 상류 룰이
그 이름으로 문서를 찾는다.

수동 검증: 영어 프로젝트에서 설문 생성 → 시크릿 창 응답 → 리포트 언어를
확인했다."
```

---

## 완료 확인

모든 태스크가 끝난 뒤 아래를 돌린다.

- [ ] **전체 테스트**

Run: `cd frontend && npx tsc --noEmit && npx vitest run 2>&1 | tail -5`
Run: `cd frontend && npm run build 2>&1 | tail -6`
Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`

Expected: 프론트엔드 기존 664개 테스트가 그대로 통과하고 신규가 더해져 있다. 백엔드 기존 878개도 같다.

- [ ] **e2e는 돌리지 않는다**

`npm run test:e2e`는 **포트 3000을 겨냥한다**. `proto-config/CLAUDE.md`의 §"프로세스·포트"가 기록한 사고가 그것이다 — Playwright가 포트 3000의 프로세스를 SIGKILL했고 그것은 Pathfinder 프론트엔드였다. 이 계획의 검증은 vitest와 수동 확인으로 한다.

- [ ] **수동 검증 총괄** (Task 10·11·12에서 각각 했다면 생략)

한국어/영어 프로젝트 각 하나로 전 흐름을 돌린다: Discovery(PR/FAQ까지) → 문서 리뷰 → 승인 → 프로토타입 빌드 → 검증 설문 → 리포트. 각 단계의 언어를 눈으로 확인한다.

- [ ] **브랜치 병합**

`superpowers:finishing-a-development-branch`를 따른다.
