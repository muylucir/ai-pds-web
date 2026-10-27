# antd 레이아웃 아키텍처 (앱 셸)

앱 셸 = `Layout` + 접히는 `Layout.Sider`(좌측 `Menu`) + `Layout.Header`(브레드크럼·사용자 메뉴) + `Layout.Content`. 셸은 라우트 그룹 레이아웃에 한 번 두고, 각 페이지는 `PageHeader`로 시작한다.

```tsx
// app/(app)/layout.tsx — 서버 컴포넌트, 셸만 감싼다
import { AppShell } from '@/components/app-shell';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
```

```tsx
// components/app-shell.tsx
'use client';
import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Avatar, Dropdown, Flex, Layout, Menu, Typography, theme } from 'antd';
import type { MenuProps } from 'antd';
import { DashboardOutlined, LogoutOutlined, ShoppingCartOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons';

const { Header, Sider, Content } = Layout;

const NAV: { key: string; icon: React.ReactNode; label: string }[] = [
  { key: '/', icon: <DashboardOutlined />, label: '대시보드' },
  { key: '/orders', icon: <ShoppingCartOutlined />, label: '주문 관리' },
  { key: '/customers', icon: <TeamOutlined />, label: '고객' },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  // basePath가 빠진 경로. 끝 슬래시 유무에 기대지 않도록 정규화한다(trailingSlash: true).
  const pathname = usePathname().replace(/\/+$/, '') || '/';
  const { token } = theme.useToken();

  // 가장 길게 일치하는 메뉴를 선택 상태로 — /orders/123 에서도 '주문 관리'가 켜진다.
  const selected = NAV.map((n) => n.key)
    .filter((k) => (k === '/' ? pathname === '/' : pathname === k || pathname.startsWith(k + '/')))
    .sort((a, b) => b.length - a.length)[0];

  const items: MenuProps['items'] = NAV.map((n) => ({
    key: n.key, icon: n.icon, label: <Link href={n.key}>{n.label}</Link>,
  }));

  const userMenu: MenuProps = {
    items: [{ key: 'logout', icon: <LogoutOutlined />, label: '로그아웃' }],
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={220} theme="light"
             style={{ borderInlineEnd: `1px solid ${token.colorBorderSecondary}` }}>
        <Flex align="center" style={{ height: 64, paddingInline: token.paddingLG }}>
          <Typography.Text strong>{collapsed ? 'OP' : 'Order Portal'}</Typography.Text>
        </Flex>
        <Menu mode="inline" items={items} selectedKeys={selected ? [selected] : []}
              style={{ borderInlineEnd: 'none' }} />
      </Sider>
      <Layout>
        <Header style={{ background: token.colorBgContainer, paddingInline: token.paddingLG,
                         borderBottom: `1px solid ${token.colorBorderSecondary}` }}>
          <Flex justify="flex-end" align="center" style={{ height: '100%' }}>
            <Dropdown menu={userMenu}>
              <Flex gap="small" align="center" style={{ cursor: 'pointer' }}>
                <Avatar size="small" icon={<UserOutlined />} />
                <Typography.Text>홍길동</Typography.Text>
              </Flex>
            </Dropdown>
          </Flex>
        </Header>
        <Content style={{ padding: token.paddingLG }}>{children}</Content>
      </Layout>
    </Layout>
  );
}
```

- `Link`는 `trailingSlash: true`에 맞춰 `/orders/`로 바꿔 내보낸다 — 메뉴 키는 슬래시 없이 두고 현재 경로만 정규화해 비교한다.
- 사이드바 대신 상단 메뉴가 맞는 서비스면 `Header` 안에 `Menu mode="horizontal"`.

## 페이지 헤더

모든 페이지는 브레드크럼 + 제목 + 보조 설명 + 우측 액션으로 시작한다. ProLayout의 PageContainer는 antd 6과 맞지 않으므로 직접 둔다.

```tsx
// components/page-header.tsx
'use client';
import { Breadcrumb, Flex, Space, Typography } from 'antd';
import type { BreadcrumbProps } from 'antd';

export function PageHeader({ title, description, breadcrumb, extra, tags }: {
  title: string; description?: string; breadcrumb?: BreadcrumbProps['items'];
  extra?: React.ReactNode; tags?: React.ReactNode;
}) {
  return (
    <Flex vertical gap="small" style={{ marginBottom: 24 }}>
      {breadcrumb && <Breadcrumb items={breadcrumb} />}
      <Flex justify="space-between" align="flex-start" gap="middle" wrap>
        <div>
          <Space size="small" align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{title}</Typography.Title>
            {tags}
          </Space>
          {description && <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>{description}</Typography.Paragraph>}
        </div>
        {extra && <Space wrap>{extra}</Space>}
      </Flex>
    </Flex>
  );
}
```

브레드크럼 항목은 `{ title: <Link href="/orders/">주문 관리</Link> }` 형태로 넘긴다.

## 공개 페이지 (로그인·오류)

셸 밖 라우트(`app/login/page.tsx`)에서 뷰포트 가운데 고정폭 `Card`. 403/404/500은 `Result`(`status="403"` 등) + 돌아가기 `Button`.

```tsx
<Flex justify="center" align="center" style={{ minHeight: '100vh' }}>
  <Card title="로그인" style={{ width: 380 }}>{/* Form */}</Card>
</Flex>
```
