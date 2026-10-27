---
name: antd-design
description: >
  Invoke for React UI development when the prototype uses Ant Design (antd v6) — the spec or the user asked for
  Ant Design, or the prototype's package.json already depends on `antd`. Primary skill for: enterprise / back-office
  screens on antd + Next.js App Router — Layout + Sider + Menu app shell, dense data tables (antd Table with
  filters, sorting, row selection, pagination), antd Form with multi-column layouts, Steps wizards, Card + Statistic
  dashboards, Descriptions detail views, brand theming through ConfigProvider tokens, and AI streaming chat with
  @ant-design/x. antd is an npm dependency (not copy-in source). Skip for: shadcn/ui prototypes (use shadcn-design),
  Vue/Angular/React Native, backend/infrastructure work.
---

# Ant Design (antd v6) Design System

Build React UIs with Ant Design v6 on Next.js App Router. antd은 npm 패키지로 들어오고(소스를 복사하지 않는다), 화면의 모든 시각 값은 `ConfigProvider`의 디자인 토큰에서 나온다. 업무 시스템(관리 콘솔·백오피스·운영 화면)답게 **정보 밀도 높은 화면**을 만드는 것이 이 스킬의 목적이다.

## 언제 이 스킬을 쓰는가

- 스펙이나 사용자가 Ant Design을 요청했을 때.
- **개선 세션에서 `prototype/package.json`에 `antd`가 이미 있으면** 계속 이 스킬을 쓴다. 한 프로토타입 안에서 shadcn과 antd를 섞지 않는다 — 두 시스템은 토큰·리셋 CSS·폼 모델이 달라 섞으면 화면이 깨진다.

## Golden Rule: antd 컴포넌트 우선 (커스텀 재발명 금지)

새 UI가 필요하면 먼저 antd 컴포넌트가 있는지 확인한다(`references/components.md`). 수기 `<div>` 스타일링·직접 만든 표·모달 대신 `Table`/`Modal`/`Drawer`/`Descriptions`/`Tag`/`Empty`/`Skeleton`/`Result`를 쓴다. 색·여백·라운드는 하드코딩하지 않고 토큰(`theme.useToken()`)에서 읽는다.

## Installation

```bash
npm install antd @ant-design/icons @ant-design/nextjs-registry
# AI 채팅이 있으면
npm install @ant-design/x react-markdown remark-gfm react-syntax-highlighter
npm install -D @types/react-syntax-highlighter
```

- **antd 6.x + `@ant-design/icons` 6.x를 함께** 쓴다(icons 6은 antd 5와 호환되지 않는다). React 18 이상이 필요하고, Next.js 15의 React 19에서는 패치 패키지가 필요 없다.
- **`@ant-design/pro-components`(ProLayout·ProTable·ProForm)는 쓰지 않는다** — peer 범위가 antd `^4 || ^5`라 antd 6과 맞지 않는다. 앱 셸·표·폼은 antd 기본 컴포넌트로 구성한다(`references/layout.md`, `references/patterns.md`).
- 차트가 필요하면 `@ant-design/charts`(브라우저 전용 — `next/dynamic`으로 `ssr: false`, `references/patterns.md` dashboard).

## Root layout — Registry · Provider · Theme

App Router에서는 `AntdRegistry`가 SSR 시 CSS-in-JS 스타일을 수집해 첫 화면부터 스타일이 붙게 한다. 테마·로케일·`App`(message/modal/notification 컨텍스트)은 클라이언트 `Providers` 하나에 모은다.

```tsx
// app/layout.tsx — 서버 컴포넌트
import { AntdRegistry } from '@ant-design/nextjs-registry';
import './globals.css';
import './aipds-theme.css';          // 브랜드 프로필이 있을 때만 — references/brand-theme.md
import { brandToken } from './brand-token';
import { Providers } from './providers';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <AntdRegistry>
          <Providers token={brandToken()}>{children}</Providers>
        </AntdRegistry>
      </body>
    </html>
  );
}
```

```tsx
// app/providers.tsx
'use client';
import { App, ConfigProvider, theme } from 'antd';
import type { ThemeConfig } from 'antd';
import koKR from 'antd/locale/ko_KR';   // 화면 언어가 영어면 antd/locale/en_US
import dayjs from 'dayjs';
import 'dayjs/locale/ko';

dayjs.locale('ko');

export function Providers({ token, children }: { token: ThemeConfig['token']; children: React.ReactNode }) {
  return (
    <ConfigProvider locale={koKR} theme={{ token, algorithm: theme.defaultAlgorithm }}>
      <App>{children}</App>
    </ConfigProvider>
  );
}
```

- **`algorithm`은 함수라서 서버 컴포넌트에서 props로 넘길 수 없다** — 그래서 `Providers`가 클라이언트에서 붙인다. 서버에서 넘기는 것은 직렬화 가능한 `token` 객체뿐이다.
- 브랜드 프로필이 없으면 `aipds-theme.css` import와 `brandToken`을 빼고 `token`을 생략한다(antd 기본 테마).
- 로케일은 **개시 프롬프트가 정한 화면 언어**를 따른다(`ko_KR`/`en_US`). DatePicker 문구는 dayjs 로케일도 함께 맞춘다.

## Documentation Access

컴포넌트 API는 온디맨드로 조회한다(references는 인덱스와 패턴만). 공식 문서는 LLM용 마크다운을 제공한다:

- 컴포넌트 문서: `https://ant.design/components/{name}.md` (예: `table.md`, `form.md`, `descriptions.md`)
- 컴포넌트 DOM 구조·`classNames`/`styles` 슬롯: `https://ant.design/components/{name}/semantic.md`
- 전체 색인: `https://ant.design/llms.txt`, 디자인 언어: `https://ant.design/design.md`
- AI 채팅 컴포넌트(@ant-design/x): `https://x.ant.design/components/{name}` (예: `bubble`, `sender`)

**v6에서 이름이 바뀐 prop이 많다.** 기억에 의존하지 말고 `references/components.md`의 변경표나 컴포넌트 문서를 확인한다 — 옛 이름은 동작은 하지만 콘솔 경고를 내고 v7에서 사라진다.

## Page Layout Architecture

앱 셸 = `Layout` + 접히는 `Layout.Sider`(좌측 `Menu`) + `Layout.Header`(브레드크럼·사용자 메뉴) + `Layout.Content`. 각 페이지 상단은 제목·설명·주요 액션을 담은 **페이지 헤더**로 시작한다. 로그인/오류 같은 공개 페이지는 셸 밖에서 가운데 정렬 `Card`. 상세 코드: `references/layout.md`.

## Component Selection Guide (화면 유형 → antd 구성)

| 화면 유형 | antd 구성 |
|---|---|
| 목록(table-view) | `Card` 안에 툴바(`Input.Search`·`Select` 필터·주요 `Button`) + `Table`(`columns`의 `sorter`/`filters`, `rowSelection` 일괄 작업, `pagination`) + 상태 `Tag` |
| 입력(form) | `Form layout="vertical"` + `Row`/`Col` 다열 배치 + `Form.Item rules` 검증 + 하단 고정 액션 바 |
| 단계 입력(wizard) | `Steps`(`items`) + 단계별 필드 묶음 + `form.validateFields(단계 필드)` 후 진행 |
| 대시보드(dashboard) | `Row`/`Col` 그리드의 `Card` + `Statistic`(KPI) + 차트 + 최근 항목 `Table`(`size="small"`) |
| 상세(detail) | 페이지 헤더(상태 `Tag`·액션) + `Descriptions bordered` + `Tabs`(관련 목록·이력 `Timeline`) |
| 채팅(chat) | `@ant-design/x`의 `Bubble.List` + `Sender`, 본문은 react-markdown — `references/ai-streaming.md` |

상세 패턴: `references/patterns.md`.

## 엔터프라이즈 밀도 — 화면이 가벼워 보이지 않게

업무 사용자가 "실제 시스템 같다"고 느끼는 것은 컴포넌트가 아니라 **화면에 담긴 정보의 양과 구조**다.

- **현실적인 데이터 양**: 목록은 목업 20–50행, 열 6–10개(ID·이름·상태·담당자·금액·일시…). 3행짜리 표는 데모처럼 보인다.
- **모든 목록에 툴바**: 검색 + 필터 1–3개 + 주요 액션 + 선택 시 일괄 작업. 표 위에 결과 건수.
- **상태는 `Tag`/`Badge`로**: 상태값마다 색이 다른 `Tag`(`success`/`processing`/`warning`/`error`/`default`).
- **페이지 헤더**: 모든 페이지에 `Breadcrumb` + 제목 + 보조 설명 + 우측 액션 버튼.
- **상세는 `Descriptions bordered`**: 필드 10개 이상을 2–3열로. 텍스트 나열 금지.
- **요약 카드**: 목록·대시보드 상단에 `Statistic` 카드 3–4개(전체/진행/지연/완료 등).
- **표는 `size="middle"`** 이 기본, 행이 많거나 보조 표는 `size="small"`. 전체를 조밀하게 하려면 `Providers`의 `algorithm`에 `theme.compactAlgorithm`을 더한다(`[theme.defaultAlgorithm, theme.compactAlgorithm]`).
- **빈 상태·로딩·오류도 화면이다**: `Empty`, `Skeleton`/`Table loading`, `Result`(403/404/500)를 갖춘다.

## Key Conventions

- **antd 컴포넌트를 쓰는 파일은 `'use client'`**. App Router의 서버 컴포넌트에서는 `Select.Option` 같은 점 표기 하위 컴포넌트가 동작하지 않는다 — 페이지 본문은 클라이언트 컴포넌트로 분리하고, 서버 `page.tsx`는 그것을 렌더만 한다. 하위 항목은 가능한 한 `items`/`options` prop으로 넘긴다(v6의 정본 방식).
- **message/modal/notification은 `App.useApp()`으로**: `const { message, modal } = App.useApp();`. 정적 `message.success()`·`Modal.confirm()`은 `ConfigProvider` 테마·로케일을 받지 못한다.
- **스타일링**: 여백은 `Space`/`Flex`의 `gap`과 `Row gutter`로. 커스텀 스타일이 필요하면 `theme.useToken()`의 `token.colorPrimary`/`token.marginLG`/`token.borderRadiusLG` 같은 값을 쓴다 — `#1677ff` 같은 raw 색·매직 넘버 금지(브랜드가 닿지 않는다). 컴포넌트 내부 스타일은 `classNames`/`styles` 슬롯으로(내부 DOM 클래스를 CSS로 덮어쓰지 않는다 — v6에서 DOM 구조가 바뀌었다).
- **아이콘**: `@ant-design/icons`에서 컴포넌트로 import(`import { PlusOutlined } from '@ant-design/icons'`), 버튼에는 `icon={<PlusOutlined />}`.
- **텍스트**: 제목·본문·보조 문구는 `Typography.Title`/`Text`/`Paragraph`(`type="secondary"`). 날짜는 dayjs.
- **폼 검증은 `Form.Item rules`가 정본**이다. API 요청 스키마를 zod로 두었다면 제출 직전 `schema.parse(values)`로 한 번 더 확인할 수 있지만, 필드 오류 표시는 antd Form에 맡긴다.
- **`trailingSlash`/`basePath`**: `Link`와 `router.push`는 basePath를 자동으로 붙인다. `Menu`의 `label`에는 `<Link href="/orders">`를 넣고, 선택 상태는 `usePathname()`으로 계산한다(basePath가 빠진 경로가 나온다).

## Brand profile

작업 디렉토리 `CLAUDE.md`에 브랜드 섹션이 있으면 그 값을 antd 토큰으로 옮긴다 — antd는 CSS 변수(`--primary`)를 읽지 않으므로, `aipds-theme.css`를 import하는 것만으로는 화면에 브랜드가 닿지 않는다. 변수 → 토큰 매핑과 `brandToken()` 구현: `references/brand-theme.md`.

## Accessibility

antd 컴포넌트는 키보드 탐색·포커스 관리·ARIA 속성을 내장한다. `Modal`/`Drawer`에는 `title`을, 아이콘만 있는 버튼에는 `aria-label`을, 폼 필드에는 `Form.Item label`을 준다.

## References

- `references/components.md` — 용도별 컴포넌트 카탈로그 + **v6 prop 이름 변경표** + 사용 규칙
- `references/layout.md` — Layout/Sider/Menu 앱 셸 + 페이지 헤더 + 공개 페이지
- `references/patterns.md` — 화면 유형별 구성(table-view/form/wizard/dashboard/detail)
- `references/brand-theme.md` — 브랜드 프로필(`aipds-theme.css`) → antd 토큰
- `references/ai-streaming.md` — 서버 라우트(Strands→SSE)·`useAIStreaming` 훅 + antd·@ant-design/x 채팅 렌더
