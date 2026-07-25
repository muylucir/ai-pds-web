# shadcn 레이아웃 아키텍처 (앱 셸 — cloudscape AppLayout 대응)

## 보호 라우트 앱 셸 — sidebar 블록

cloudscape의 AppLayout + TopNavigation(밖) + SideNavigation 대응 = shadcn **sidebar 블록**.

```tsx
// src/app/(protected)/layout.tsx
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="flex h-14 items-center gap-2 border-b px-4">
          <SidebarTrigger />          {/* 사이드바 토글 */}
          {/* 브레드크럼/타이틀/사용자 메뉴 — cloudscape TopNavigation 대응 */}
        </header>
        <main className="flex flex-1 flex-col gap-4 p-4">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
```

- `AppSidebar`(`@/components/app-sidebar.tsx`): `Sidebar`/`SidebarHeader`/`SidebarContent`/`SidebarMenu`/`SidebarMenuItem`으로 네비 그룹. `architecture.json.layout_components[].side_navigation.sections[]`를 메뉴로.
- **헤더는 `SidebarInset` 안 최상단**(sidebar 블록 관례). cloudscape "TopNavigation은 AppLayout 밖" 규칙의 shadcn 대응.
- 블록 참고: WebFetch `https://ui.shadcn.com/blocks` (sidebar-* 블록).

## 공개 / standalone 페이지 (login/forbidden/error) — 앱 셸 밖

셸(SidebarProvider) **밖**에서 뷰포트 중앙 고정폭 카드로. 전체 폭 셸 금지(실제 렌더가 full-width로 틀어짐).

```tsx
// src/app/login/page.tsx (protected 그룹 밖)
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

export default function LoginPage() {
  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader><CardTitle>로그인</CardTitle></CardHeader>
        <CardContent>{/* 폼 */}</CardContent>
      </Card>
    </div>
  );
}
```

## RSC-by-default (CLAUDE.md Rule 6/7)

- `page.tsx`는 기본 Server Component. 데이터는 서버에서 fetch.
- `"use client"`는 Radix 상호작용/이벤트/훅이 필요한 island 컴포넌트에만(예: `DataTable`, 폼, 사이드바 토글, 채팅). page는 island를 import만.
