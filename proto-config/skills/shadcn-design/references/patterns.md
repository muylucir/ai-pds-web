# shadcn ui_type 패턴 레퍼런스

`architecture.json`의 `page_type`(=ui_type, DS-중립) → shadcn 구성. 상세 블록은 WebFetch `https://ui.shadcn.com/blocks`.

## table-view — Table + TanStack Table (필수 패턴)

cloudscape `useCollection` 대응 = `@tanstack/react-table`의 `useReactTable`.

```tsx
'use client';
import { useReactTable, getCoreRowModel, getFilteredRowModel, getSortedRowModel, getPaginationRowModel, flexRender, type ColumnDef } from '@tanstack/react-table';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

export function DataTable<T>({ columns, data }: { columns: ColumnDef<T>[]; data: T[] }) {
  const [globalFilter, setGlobalFilter] = useState('');
  const table = useReactTable({
    data, columns,
    state: { globalFilter },
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });
  // <Input> 필터 + <Table> 렌더(flexRender) + <Button> 페이지네이션(previousPage/nextPage)
}
```

- 서버 페이지네이션(커서 `{items, nextToken}` — CLAUDE.md API Contract)이면 `manualPagination: true` + 훅에서 nextToken 전달.
- 정렬 헤더: `column.toggleSorting()`. 필터: `getFilteredRowModel`.

## form / wizard — react-hook-form + zod

shadcn `Form`은 react-hook-form을 감싼다. 요청 스키마는 zod(`z.infer`로 타입 도출 — BE와 공유). **RHF + zod resolver가 하네스 정본**(`api-contract-zod` 스킬 + `[J]` 계약) — 검증/제출 배선은 여기서 벗어나지 않는다.

```tsx
'use client';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage } from '@/components/ui/form';
// const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
// <Form {...form}><FormField .../></Form>
```

**필드 레이아웃·컨트롤 선택 (공식 forms 규칙 — RHF 위에 얹어 정합)**:
- **레이아웃은 `Field`/`FieldGroup`**(raw `div` + `space-y-*` 금지). shadcn `Form`의 `FormItem`을 `Field`로 대체하거나, 비-Form 폼은 `FieldGroup > Field`로. 관련 필드 묶음은 `FieldSet` + `FieldLegend`(divs + heading 금지).
- **컨트롤 선택**: 텍스트→`Input` / 드롭다운→`Select` / 검색형→`Combobox` / 불리언→`Switch`(설정)·`Checkbox`(폼) / 단일선택→`RadioGroup` / **2–7 지선다→`ToggleGroup` + `ToggleGroupItem`**(수동 버튼 루프+상태 금지) / 멀티라인→`Textarea` / 인증코드→`InputOTP`.
- **입력 앞뒤 아이콘·버튼**은 `InputGroup` + `InputGroupAddon`(절대 위치 금지). raw `Input`/`Textarea`를 `InputGroup`에 직접 넣지 말고 `InputGroupInput`/`InputGroupTextarea`를 쓴다.
- **검증·disabled 표기는 둘 다**: 컨테이너엔 `data-invalid`/`data-disabled`(스타일), 컨트롤엔 `aria-invalid`/`disabled`(접근성+스타일). 에러 메시지는 `FormMessage`.

- **wizard**: multi-step은 step 상태(`useState`) + step별 zod 부분스키마 + `Tabs` 또는 커스텀 stepper. 마지막 step에서 전체 제출.

## Base UI vs Radix — 컴포넌트 구현 분기 (생성 전 반드시 확인)

shadcn은 이제 **두 프리미티브 백엔드**를 지원한다 — 전통 **Radix**와 신규 **Base UI**. 같은 컴포넌트라도 prop 시그니처가 달라 **잘못 쓰면 런타임/타입 에러**가 난다. 프로젝트가 어느 쪽인지 `components.json`의 `base` 필드 또는 `npx shadcn@latest info`로 확인한 뒤 코드를 생성한다.

| 관심사 | Radix | Base UI |
|---|---|---|
| 커스텀 트리거로 교체 | `asChild`: `<DialogTrigger asChild><Button/></DialogTrigger>` | `render`: `<DialogTrigger render={<Button/>}>Open</DialogTrigger>` |
| `render`가 비-button(a/span) 생성 | 해당 없음 | `nativeButton={false}` 추가 |
| Select 옵션 | 인라인 JSX(`<SelectItem>`), 단일선택·문자열 | 루트에 `items` prop 배열, 다중선택·객체값 가능 |
| ToggleGroup | `type="single"｜"multiple"`, `defaultValue` 문자열 | `multiple` boolean, `defaultValue` 항상 배열 |
| Slider | `defaultValue={[50]}` (항상 배열) | `defaultValue={50}` (단일 thumb는 숫자) |
| Accordion | `type="single"｜"multiple"` 필수, `defaultValue` 문자열 | `type` 없음·`multiple` boolean, `defaultValue` 배열 |

- **불명 시 Radix 전제**가 안전(기존 다수). 단 `info`로 확정 가능하면 확정한다.
- 서드파티 레지스트리에서 add한 컴포넌트는 import 경로가 어긋날 수 있으니 생성 후 `@/components/ui/*` 경로로 교정한다.

## dashboard — Card 그리드 + chart

`Card` 그리드(`grid gap-4 md:grid-cols-2 lg:grid-cols-4`) + KPI는 `Card` + `Badge`. 차트는 shadcn `chart`(recharts 래퍼). `architecture.json`/domain KPI를 위젯으로.

## detail — Card + Tabs

`Card`(헤더/콘텐츠) + `Separator` + description list(`dl`) + 섹션이 많으면 `Tabs`. 상태는 `Badge`.

## chat — AI 스트리밍

메시지 목록 + `MarkdownContent`(assistant) + `Textarea`+`Button` 입력. 스크롤 컨테이너는 **`MessageScroller`(공식 채팅 프리미티브 — 자동 하단 고정/위치 복원)를 선택 채택**하거나 `ScrollArea`로 구성한다. 본문 렌더(`react-markdown`)와 스트리밍(`useAIStreaming` SSE 계약)은 하네스 SSOT이므로 어느 쪽을 쓰든 유지한다. 상세는 `references/ai-streaming.md`([J] 계약).
