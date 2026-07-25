---
name: shadcn-design
description: >
  Invoke for React UI development when the active design system is shadcn/ui (architecture.json.design_system = "shadcn").
  Primary skill for: shadcn/ui + Radix + Tailwind layouts (sidebar shell, header), data tables with TanStack Table
  (filtering/sorting/pagination), react-hook-form + zod forms, multi-step wizards, dashboards, CRUD pages,
  detail views, and AI streaming chat interfaces in React. shadcn uses a copy-in component model (source owned in
  @/components/ui, no barrel imports). Skip for: Cloudscape (use cloudscape-design), Ant Design (use antd-design),
  Vue/Angular/React Native, backend/infrastructure work.
---

# shadcn/ui Design System

Build React UIs with shadcn/ui — Radix UI primitives + Tailwind CSS, delivered as copy-in source (you own the code in `@/components/ui/`, not an npm dependency). This is the DS adapter for the harness when `architecture.json.design_system === "shadcn"`. 계약 SSOT: `.pipeline/scripts/design-systems.json`의 shadcn 엔트리.

## Golden Rule: shadcn 컴포넌트 + Radix 프리미티브 우선 (커스텀 재발명 금지)

새 UI가 필요하면 먼저 shadcn 컴포넌트(`@/components/ui/*`)나 Radix 프리미티브가 있는지 확인한다. 커스텀 CSS/styled-components/hand-rolled 요소를 쓰기 전에 shadcn 카탈로그(`references/components.md`)를 본다. shadcn은 copy-in 모델이라 `npx shadcn@latest add {name}`으로 소스를 프로젝트에 들여온 뒤 `@/components/ui/{name}`에서 개별 import한다.

## Installation

```bash
# Tailwind + shadcn 초기화
npx shadcn@latest init          # components.json 생성 (paths, tailwind, aliases)
# 컴포넌트 추가 (필요한 것만 copy-in)
npx shadcn@latest add button input table dialog form sidebar tabs card badge sonner
```

핵심 의존성(design-systems.json.npm_packages SSOT): `tailwindcss`, `class-variance-authority`, `clsx`, `tailwind-merge`, `@radix-ui/react-*`, `@tanstack/react-table`(테이블), `react-hook-form`+`@hookform/resolvers`+`zod`(폼), `lucide-react`(아이콘), `react-markdown`+`remark-gfm`(AI 스트리밍).

- **`@/components/ui/{name}`에서 개별 import** (배럴 없음 — shadcn은 파일별 소스). 예: `import { Button } from "@/components/ui/button"`.
- `cn()` 유틸(`@/lib/utils`, clsx+tailwind-merge)로 클래스 병합. 인라인 스타일/매직 넘버 금지 — Tailwind 스페이싱 스케일 사용(`references/foundations.md`).

### Dark mode & theme (runtime)
CSS 변수 기반 테마(`references/foundations.md`). `.dark` 클래스로 다크모드 토글(next-themes 권장).

## Documentation Access

컴포넌트 상세 API/예제는 온디맨드로 조회한다(references는 인덱스만). **CLI가 WebFetch보다 정확**하다(설치본 소스·프로젝트 컨텍스트 반영):
- **프로젝트 컨텍스트**: `npx shadcn@latest info` — `aliases`/`isRSC`/`tailwindVersion`/`base`(Radix vs Base UI)/`iconLibrary`/`packageManager`. **codegen 전 `base`/`iconLibrary` 확인 필수**(아래 Key Conventions·`references/patterns.md` Base vs Radix).
- **소스 확인**: `npx shadcn@latest view @shadcn/{name}` (설치될 실제 파일 내용).
- **예제/문서 URL**: `npx shadcn@latest docs {name}`.
- **업데이트 미리보기**: `npx shadcn@latest add {name} --dry-run --diff` (기존 파일 덮어쓰기 전 diff).
- WebFetch 폴백: 컴포넌트 `https://ui.shadcn.com/docs/components/{name}`, 블록 `https://ui.shadcn.com/blocks`.

## Page Layout Architecture (C2 — cloudscape AppLayout 대응)

앱 셸은 **sidebar 블록**으로 구성한다(cloudscape AppLayout+TopNavigation+SideNavigation 대응):

```tsx
// src/app/(protected)/layout.tsx — 보호 라우트 앱 셸
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { AppHeader } from "@/components/app-header";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <AppSidebar />               {/* 좌측 네비 (cloudscape SideNavigation 대응) */}
      <SidebarInset>
        <AppHeader />              {/* 상단 헤더 (cloudscape TopNavigation 대응 — Inset 최상단) */}
        <main className="flex flex-1 flex-col gap-4 p-4">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
```

- **상단 헤더는 `SidebarInset` 안 최상단**(cloudscape의 "TopNavigation은 AppLayout 밖" 규칙에 대응하는 shadcn 관례 — sidebar 블록 구조).
- **공개/standalone 페이지**(login/forbidden/error)는 앱 셸 **밖**에서 뷰포트 중앙 고정폭 카드(`<Card className="mx-auto max-w-sm">`)로. 셸을 씌우지 않는다.
- 상세: `references/layout.md`.

## Component Selection Guide (C1 — ui_type → shadcn 매핑)

`architecture.json`의 `page_type`(=ui_type, DS-중립) → shadcn 구성. 상세 패턴은 `references/patterns.md`.

| ui_type | shadcn 구성 |
|---|---|
| `table-view` | `Table` + **TanStack Table**(`useReactTable`) — 필터/정렬/페이지네이션. DataTable 패턴 |
| `form` | `Form`(react-hook-form + zod resolver, 정본) + `Field`/`FieldGroup` 레이아웃 + Input/Select/Combobox/ToggleGroup/… + `data-invalid`/`aria-invalid` |
| `wizard` | multi-step Form + `Tabs`/`Stepper` 커스텀 + step 상태 |
| `dashboard` | `Card` 그리드 + 차트(recharts, shadcn `chart`) + `Badge` KPI |
| `detail` | `Card` + `Separator` + description list + `Tabs` |
| `chat` | 스크롤 메시지 목록 + `MarkdownContent`(react-markdown) + 입력 `Textarea`+`Button` — **AI 스트리밍**. 코드 예제가 오가면 `CodeBlock`(react-syntax-highlighter 문법 강조 + 복사, 패턴 2)이 **필수**. (선택) **AI Elements**(`conversation`/`message`/`sources`/`tool`)로 강화 — AI SDK 훅 배제, `useAIStreaming` 배선(패턴 4) |

## Common Implementation Patterns

### Table with TanStack Table (필수 — cloudscape useCollection 대응)
목록 화면은 `@tanstack/react-table`의 `useReactTable`로 필터/정렬/페이지네이션을 구현한다(cloudscape의 `useCollection` 역할). 상세 코드: `references/patterns.md`.

### AI Chat — Markdown 스트리밍 ([J] 게이트 계약 — 필수)
AI FR이 있으면 `check-markdown-render.mjs`([J])가 강제한다(DS-무관): (1) `react-markdown`+`remark-gfm` 의존성 (2) `useAIStreaming` 훅 사용 (3) `MarkdownContent`/`ReactMarkdown` JSX로 렌더 (4) **chat `ui_type`이면 코드 문법 강조 하이라이터**(`react-syntax-highlighter` 등) 의존성 + code 컴포넌트 배선. **마크다운 파싱·하이라이터 존재는 DS-무관**이고, shadcn 어댑터는 코드블록(하이라이트 테마)/링크 렌더만 shadcn 스타일로 매핑한다. shadcn엔 Cloudscape `CodeView` 같은 내장 하이라이터가 없으므로 `CodeBlock`을 명시 생성해야 한다 — 죽은 `language-*` 클래스로 끝내면 FR "코드 블록 문법 강조" AC 미충족. 상세: `references/ai-streaming.md` 패턴 2.

> **AI Elements (선택 — chat/AI-native `ui_type`)**: shadcn 위에 얹은 Vercel의 AI 특화 레지스트리(`conversation`/`message`/`sources`/`inline-citation`/`reasoning`/`tool`/`prompt-input` — copy-in). 손수 조립 대신 성숙한 AI UI를 쓸 수 있다. **단 프레젠테이션 컴포넌트만 채택하고 AI SDK 데이터 훅(`useChat` 등)은 배제**한다 — 데이터는 하네스 `useAIStreaming`, 본문은 `react-markdown`을 유지해야 `[J]` 게이트를 통과한다(경계·배선 예시는 `references/ai-streaming.md` 패턴 4).

### Form with react-hook-form + zod
shadcn `Form`은 react-hook-form을 감싼다. 요청 스키마는 zod(`z.infer`로 타입 도출 — CLAUDE.md API Contract). 상세: `references/patterns.md`.

### Dialog / Delete Confirmation
`AlertDialog`(파괴적 확인) / `Dialog`(일반 모달). Radix 기반이라 포커스 트랩/ESC/오버레이 내장.

## Key Conventions

- **개별 import** `@/components/ui/{name}` (배럴 금지 — 애초에 없음). ESLint no-restricted-imports 불필요(구조상).
- **Base UI vs Radix 확인 (codegen 전 필수)**: `npx shadcn@latest info`의 `base` 필드로 판별. `asChild`(Radix) vs `render`+`nativeButton={false}`(Base UI), Select/ToggleGroup/Slider/Accordion prop 시그니처가 다르다 — `references/patterns.md` Base vs Radix 표. 불명 시 Radix 전제.
- **스타일링(공식 규칙)**: `cn()`으로 클래스 병합(템플릿 리터럴 삼항 금지), Tailwind 스페이싱 스케일만(매직 넘버 금지). **간격은 `flex`/`grid`+`gap-*`**(`space-y-*`/`space-x-*` 금지), 정사각형은 `size-*`, 색은 시맨틱 토큰(`bg-primary`/`text-muted-foreground` — raw `bg-blue-500`/수동 `dark:` 오버라이드 금지), 외형은 built-in `variant` 우선(수동 className 지양). 오버레이 z-index 수동 지정 금지.
- **아이콘(공식 규칙)**: 컴포넌트 내부 아이콘엔 sizing 클래스(`size-4`) 금지 — 버튼 아이콘은 `data-icon="inline-start｜inline-end"`. 아이콘은 컴포넌트 객체로 전달(문자열 키 금지), import는 `iconLibrary` 설정 따름. 상세 `references/foundations.md`.
- **컴포넌트 우선(수기 마크업 금지)**: `<hr>`→`Separator`, 상태 span→`Badge`, 빈 상태→`Empty`, 로딩→`Skeleton`/`Spinner`+`disabled`, 콜아웃→`Alert`, 토스트→`sonner`. Dialog/Sheet/Drawer는 `*Title` 필수(숨기면 `sr-only`), Avatar는 `AvatarFallback` 필수, 항목은 부모 그룹에 중첩(`SelectItem`→`SelectGroup`, `TabsTrigger`→`TabsList`). 상세 `references/components.md`.
- `"use client"`는 이벤트 핸들러/훅/Radix 상호작용 컴포넌트에만(RSC-by-default — CLAUDE.md Rule 6/7).
- 폼=react-hook-form+zod(정본) + `Field`/`FieldGroup` 레이아웃 + `data-invalid`/`aria-invalid` 검증, 테이블=TanStack Table (커스텀 상태 재발명 금지).

## Accessibility

Radix 프리미티브가 ARIA 속성·키보드 네비·포커스 관리를 내장한다. `data-slot`/`data-state`/`role` 속성이 자동 부여되어 **E2E 셀렉터에 유리**(playwright-e2e 스킬의 shadcn 셀렉터 치트시트 참조).

## References

- `references/components.md` — shadcn 컴포넌트 카탈로그(폼/채팅/Empty 신 프리미티브 포함) + `npx shadcn add` 경로 + 컴포넌트 우선/접근성 규칙
- `references/patterns.md` — ui_type별 구성(table-view/form/wizard/dashboard/detail/chat) + **Base UI vs Radix 분기**
- `references/ai-streaming.md` — MarkdownContent + useAIStreaming ([J] 계약) + AI Elements(패턴 4, 선택)
- `references/layout.md` — sidebar 블록 앱 셸 + 보호/공개 라우트
- `references/foundations.md` — Tailwind 토큰/테마/스페이싱
