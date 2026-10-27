# antd 화면 유형 패턴

각 화면은 `PageHeader`(`references/layout.md`)로 시작한다. 아래 코드는 모두 클라이언트 컴포넌트(`'use client'`)이고, 서버 `page.tsx`는 그것을 렌더만 한다.

## table-view — 툴바 + Table

```tsx
'use client';
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { App, Button, Card, Flex, Input, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import { DownloadOutlined, PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

type Order = { id: string; customer: string; status: 'pending' | 'shipped' | 'delayed' | 'done'; owner: string; amount: number; createdAt: string };

const STATUS: Record<Order['status'], { color: string; label: string }> = {
  pending: { color: 'processing', label: '처리 중' },
  shipped: { color: 'blue', label: '출고' },
  delayed: { color: 'warning', label: '지연' },
  done: { color: 'success', label: '완료' },
};

export function OrderTable({ data }: { data: Order[] }) {
  const { message } = App.useApp();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<Order['status'] | undefined>();
  const [selected, setSelected] = useState<React.Key[]>([]);

  const rows = useMemo(() => data.filter((o) =>
    (!q || o.customer.includes(q) || o.id.includes(q)) && (!status || o.status === status)), [data, q, status]);

  const columns: TableProps<Order>['columns'] = [
    { title: '주문번호', dataIndex: 'id', render: (id) => <Link href={`/orders/${id}`}>{id}</Link> },
    { title: '고객', dataIndex: 'customer', sorter: (a, b) => a.customer.localeCompare(b.customer) },
    { title: '상태', dataIndex: 'status', render: (s: Order['status']) => <Tag color={STATUS[s].color}>{STATUS[s].label}</Tag> },
    { title: '담당자', dataIndex: 'owner' },
    { title: '금액', dataIndex: 'amount', align: 'right', sorter: (a, b) => a.amount - b.amount,
      render: (v: number) => `₩${v.toLocaleString()}` },
    { title: '주문일시', dataIndex: 'createdAt', sorter: (a, b) => a.createdAt.localeCompare(b.createdAt),
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm') },
    { title: '', key: 'actions', width: 80, render: () => (
      <Popconfirm title="이 주문을 취소할까요?" onConfirm={() => message.success('취소했습니다')}>
        <Button type="link" danger size="small">취소</Button>
      </Popconfirm>) },
  ];

  return (
    <Card>
      <Flex justify="space-between" gap="middle" wrap style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search placeholder="주문번호·고객 검색" allowClear onSearch={setQ} style={{ width: 240 }} />
          <Select placeholder="상태" allowClear style={{ width: 140 }} value={status} onChange={setStatus}
                  options={Object.entries(STATUS).map(([value, s]) => ({ value, label: s.label }))} />
        </Space>
        <Space wrap>
          {selected.length > 0 && <Button onClick={() => message.info(`${selected.length}건 일괄 처리`)}>선택 {selected.length}건 처리</Button>}
          <Button icon={<DownloadOutlined />}>내보내기</Button>
          <Button type="primary" icon={<PlusOutlined />}>주문 등록</Button>
        </Space>
      </Flex>
      <Typography.Text type="secondary">총 {rows.length}건</Typography.Text>
      <Table<Order> rowKey="id" columns={columns} dataSource={rows} style={{ marginTop: 8 }}
                    rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
                    pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t}건` }} />
    </Card>
  );
}
```

- **서버 페이지네이션**이면 `pagination={{ current, pageSize, total }}` + `onChange={(p) => …}`로 요청하고 `loading`을 건다. 커서 방식(`nextToken`)이면 `pagination={false}` + 하단 "더 보기" `Button`.
- 열이 많으면 `scroll={{ x: 'max-content' }}`, 고정 열은 `fixed: 'left'`.
- 목업 데이터는 20–50행을 만든다(`SKILL.md` 엔터프라이즈 밀도).

## form — 다열 Form

```tsx
'use client';
import { App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Row, Select, Space } from 'antd';

type OrderInput = { customer: string; owner: string; amount: number; dueDate: unknown; memo?: string };

export function OrderForm() {
  const [form] = Form.useForm<OrderInput>();
  const { message } = App.useApp();

  async function onFinish(values: OrderInput) {
    // await fetch(`${basePath}/api/orders/`, …)
    message.success('등록했습니다');
  }

  return (
    <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
      <Card title="기본 정보" style={{ marginBottom: 16 }}>
        <Row gutter={24}>
          <Col xs={24} md={12}>
            <Form.Item name="customer" label="고객" rules={[{ required: true, message: '고객을 선택하세요' }]}>
              <Select showSearch options={[{ value: 'c-001', label: '샘플상사' }]} />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="owner" label="담당자" rules={[{ required: true }]}><Input /></Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="amount" label="금액" rules={[{ required: true }, { type: 'number', min: 1 }]}>
              <InputNumber style={{ width: '100%' }} formatter={(v) => `₩ ${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="dueDate" label="납기일" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
          </Col>
          <Col span={24}>
            <Form.Item name="memo" label="메모"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
          </Col>
        </Row>
      </Card>
      <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
        <Button onClick={() => form.resetFields()}>초기화</Button>
        <Button type="primary" htmlType="submit">등록</Button>
      </Space>
    </Form>
  );
}
```

- 입력이 많으면 `Card` 여러 개로 묶는다(기본 정보 / 배송 / 결제).
- 서버 검증 오류는 `form.setFields([{ name: 'amount', errors: ['한도 초과'] }])`로 해당 필드에 보여준다.

## wizard — Steps + 단계 검증

```tsx
'use client';
import { useState } from 'react';
import { Button, Card, Form, Space, Steps } from 'antd';

const STEPS = [
  { title: '고객', fields: ['customer', 'owner'] },
  { title: '품목', fields: ['items'] },
  { title: '확인', fields: [] },
];

export function OrderWizard() {
  const [form] = Form.useForm();
  const [current, setCurrent] = useState(0);

  async function next() {
    await form.validateFields(STEPS[current].fields);   // 이 단계 필드만 검증
    setCurrent((c) => c + 1);
  }

  return (
    <Card>
      <Steps current={current} items={STEPS.map((s) => ({ title: s.title }))} style={{ marginBottom: 24 }} />
      {/* preserve: 숨긴 단계의 값도 유지된다 — 단계마다 Form을 따로 두지 않는다 */}
      <Form form={form} layout="vertical" preserve>
        {/* current에 따라 단계별 Form.Item 묶음을 렌더 — 지나간 단계는 hidden으로 남겨 값이 유지되게 */}
      </Form>
      <Space>
        {current > 0 && <Button onClick={() => setCurrent((c) => c - 1)}>이전</Button>}
        {current < STEPS.length - 1
          ? <Button type="primary" onClick={next}>다음</Button>
          : <Button type="primary" onClick={() => form.submit()}>제출</Button>}
      </Space>
    </Card>
  );
}
```

- 지나간 단계의 `Form.Item`은 언마운트하지 말고 `hidden`으로 둔다 — 언마운트하면 `onFinish` 값에서 빠진다.
- 마지막 확인 단계는 `Descriptions bordered`로 입력값 요약.

## dashboard — Statistic 카드 + 차트 + 최근 항목

```tsx
'use client';
import dynamic from 'next/dynamic';
import { Card, Col, Row, Statistic } from 'antd';
import { ArrowUpOutlined } from '@ant-design/icons';

// 차트 라이브러리는 브라우저 API를 쓴다 — 서버 렌더에서 제외한다.
const Line = dynamic(() => import('@ant-design/charts').then((m) => m.Line), { ssr: false });

export function Dashboard({ trend }: { trend: { date: string; orders: number }[] }) {
  return (
    <Row gutter={[16, 16]}>
      {[
        { title: '오늘 주문', value: 1284 },
        { title: '처리 중', value: 312 },
        { title: '지연', value: 27 },
        { title: '완료율', value: 94.2, suffix: '%' },
      ].map((k) => (
        <Col key={k.title} xs={24} sm={12} xl={6}>
          <Card><Statistic title={k.title} value={k.value} suffix={k.suffix} /></Card>
        </Col>
      ))}
      <Col xs={24} xl={16}>
        <Card title="주문 추이" extra={<Statistic value={8.3} suffix="%" prefix={<ArrowUpOutlined />} styles={{ content: { fontSize: 14 } }} />}>
          <Line data={trend} xField="date" yField="orders" height={280} />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card title="최근 지연 주문">{/* Table size="small" pagination={false} 5–8행 */}</Card>
      </Col>
    </Row>
  );
}
```

- KPI 카드는 3–4개, 증감은 `prefix` 아이콘 + 색(`token.colorSuccess`/`token.colorError`).
- 차트 색은 브랜드 토큰을 따르게 `theme.useToken()`의 `token.colorPrimary`를 넘긴다.

## detail — 헤더 + Descriptions + Tabs

```tsx
'use client';
import { Card, Descriptions, Table, Tabs, Tag, Timeline } from 'antd';
import type { DescriptionsProps } from 'antd';

export function OrderDetail({ order }: { order: Record<string, string> }) {
  const items: DescriptionsProps['items'] = [
    { key: 'id', label: '주문번호', children: order.id },
    { key: 'status', label: '상태', children: <Tag color="processing">처리 중</Tag> },
    { key: 'customer', label: '고객', children: order.customer },
    { key: 'owner', label: '담당자', children: order.owner },
    { key: 'amount', label: '금액', children: order.amount },
    { key: 'createdAt', label: '주문일시', children: order.createdAt },
    { key: 'address', label: '배송지', children: order.address, span: 2 },
    // … 10개 이상의 필드를 2–3열로
  ];
  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <Descriptions bordered column={{ xs: 1, md: 2, xl: 3 }} items={items} />
      </Card>
      <Card>
        <Tabs items={[
          { key: 'lines', label: '품목', children: <Table rowKey="sku" size="small" pagination={false} /> },
          { key: 'history', label: '처리 이력', children: (
            <Timeline items={[
              { title: '2026-10-01 09:12', content: '주문 접수' },
              { title: '2026-10-01 14:30', content: '출고 지시', color: 'blue' },
            ]} />) },
        ]} />
      </Card>
    </>
  );
}
```

- 페이지 헤더의 `tags`에 상태 `Tag`, `extra`에 수정·상태 변경·삭제 버튼.
- 수정은 같은 화면의 `Drawer`(`size="large"`) 안 Form으로 열면 문맥이 유지된다.
