# shadcn 디자인 토큰 / 테마 / 스페이싱

## 테마 — CSS 변수 (shadcn 기본)

shadcn은 CSS 변수로 색을 정의하고 Tailwind가 참조한다(`globals.css`). light/dark를 `:root` / `.dark`로.

```css
:root {
  --background: 0 0% 100%;
  --foreground: 0 0% 3.9%;
  --primary: 0 0% 9%;
  --muted: 0 0% 96.1%;
  --border: 0 0% 89.8%;
  /* … shadcn init이 생성 */
}
.dark { --background: 0 0% 3.9%; --foreground: 0 0% 98%; /* … */ }
```

- 색은 `bg-background`/`text-foreground`/`bg-primary`/`text-muted-foreground`/`border-border` 등 **시맨틱 토큰**으로 참조(하드코딩 hex 금지).
- 다크모드: `next-themes`의 `ThemeProvider` + `.dark` 클래스 토글.

## 스페이싱 — Tailwind 스케일 (매직 넘버 금지)

Tailwind 스페이싱 스케일만 사용(`p-2`/`gap-4`/`p-6` …). 인라인 `style={{ margin: 13 }}` 같은 매직 넘버 금지 — cloudscape의 "토큰 스케일" 규율에 대응.

- **간격은 `flex`/`grid` + `gap-*`로 준다 (`space-y-*`/`space-x-*` 금지).** 공식 shadcn 규칙 — `space-*`는 부모가 자식 마진을 주입해 병합/RTL/래핑에서 어긋난다. `<div className="flex flex-col gap-4">` / `<div className="grid gap-4 md:grid-cols-2">`.
- 컴포넌트 간격: `gap-4`(1rem) 기본, 밀집 `gap-2`, 넓게 `gap-6`.
- 페이지 패딩: `p-4`(모바일)~`p-6`(데스크톱).
- **정사각형 치수는 `size-*` 단축**(`w-10 h-10` → `size-10`), 말줄임은 `truncate`(= `overflow-hidden text-ellipsis whitespace-nowrap`).
- 클래스 병합은 `cn()`(`@/lib/utils`, clsx + tailwind-merge)로 — 조건부 클래스 충돌 방지(템플릿 리터럴 삼항 대신 `cn()`).
- **오버레이 z-index 수동 지정 금지**: Dialog/Sheet/Drawer/Popover는 스택 순서를 내부에서 관리한다(`z-50` 등 수기 부여 금지).

## 타이포그래피

- 제목: `text-2xl font-semibold`(페이지)/`text-lg font-medium`(섹션). raw `<h1>`도 Tailwind 클래스로 스타일.
- 본문: `text-sm text-muted-foreground`(보조)/`text-sm`(기본).
- 마크다운 본문: `@tailwindcss/typography`의 `prose` 클래스(AI 스트리밍 — `references/ai-streaming.md`).

## 아이콘 (공식 icon 규칙)

프로젝트에 설정된 `iconLibrary`(`components.json` — 기본 `lucide-react`, `@tabler/icons-react` 등)를 import 소스로 쓴다(라이브러리를 임의 가정하지 않는다). `import { Search } from "lucide-react"`.

- **shadcn 컴포넌트 내부 아이콘엔 sizing 클래스 금지**: `Button`/`DropdownMenuItem`/`Alert`/`Sidebar*` 등 컴포넌트 안 아이콘은 CSS가 크기를 관리한다 — `size-4`/`w-4 h-4`를 붙이지 않는다. 대신 버튼 아이콘엔 위치 attribute `data-icon="inline-start"`(접두) / `data-icon="inline-end"`(접미)를 준다.
  - ❌ `<Search className="size-4" />` (버튼 안)
  - ✅ `<Search data-icon="inline-start" />`
- **standalone(컴포넌트 밖) 아이콘**은 직접 sizing 가능: `<Search className="size-4 text-muted-foreground" />`.
- **아이콘은 컴포넌트 객체로 전달**(문자열 키 룩업 금지): `<StatusBadge icon={CheckIcon} />` — `icon: React.ComponentType`로 받아 `<Icon />` 렌더.
