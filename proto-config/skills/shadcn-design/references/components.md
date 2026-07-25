# shadcn/ui 컴포넌트 카탈로그

copy-in 모델: `npx shadcn@latest add {name}` → `@/components/ui/{name}`에서 개별 import.
상세 API/예제는 WebFetch: `https://ui.shadcn.com/docs/components/{name}`.

## Layout & Structure
| 컴포넌트 | add name | 용도 |
|---|---|---|
| Sidebar | `sidebar` | 앱 셸 좌측 네비 (SidebarProvider/Sidebar/SidebarInset/SidebarMenu) |
| Card | `card` | 콘텐츠 블록, 대시보드 위젯, 상세 섹션 |
| Separator | `separator` | 구분선 |
| Tabs | `tabs` | 탭 네비/상세 뷰 섹션 |
| Resizable | `resizable` | 분할 패널 |
| Scroll Area | `scroll-area` | 스크롤 컨테이너 (채팅 목록 등) |

## Data Display
| 컴포넌트 | add name | 용도 |
|---|---|---|
| Table | `table` | 테이블 프리미티브 (+ `@tanstack/react-table`로 DataTable) |
| Badge | `badge` | 상태 표시 (cloudscape StatusIndicator 대응). 커스텀 스타일 span 대신 사용 |
| Avatar | `avatar` | 사용자/엔티티 아바타 (**`AvatarFallback` 필수**) |
| Chart | `chart` | recharts 래퍼 (대시보드) |
| Skeleton | `skeleton` | 로딩 자리표시자 (커스텀 회색 박스 대신) |
| Empty | `empty` | 빈 상태 (cloudscape empty state 대응). `EmptyHeader`/`EmptyMedia`/`EmptyTitle`/`EmptyDescription`/`EmptyContent` 조합 — table-view/dashboard 빈 결과에 사용 |
| Spinner | `spinner` | 로딩 스피너 (버튼 pending은 `Spinner` + `disabled`, 커스텀 `isPending` prop 금지) |

## Inputs & Forms
| 컴포넌트 | add name | 용도 |
|---|---|---|
| Form | `form` | react-hook-form + zod 통합 (FormField/FormItem/FormMessage) — 하네스 정본(§forms.md) |
| Field / FieldGroup | `field` | 필드 레이아웃 컨테이너 (raw `div` + `space-y-*` 대신). `FieldSet`/`FieldLegend`로 관련 필드 묶음 |
| InputGroup | `input-group` | 입력 앞뒤 아이콘/버튼 (`InputGroupInput`/`InputGroupAddon` — 절대 위치 대신) |
| Input / Textarea | `input` `textarea` | 텍스트 입력 |
| Select | `select` | 드롭다운 (`SelectItem`은 `SelectGroup` 안에). Base UI는 `items` prop — §base-vs-radix.md |
| Combobox | `combobox` | 검색형 드롭다운 |
| Checkbox / Switch / Radio | `checkbox` `switch` `radio-group` | 불리언(Switch=설정/Checkbox=폼)/단일선택 |
| ToggleGroup | `toggle-group` | 2–7 지선다 토글 (`ToggleGroupItem` — 수동 버튼 루프 금지) |
| InputOTP | `input-otp` | 인증 코드 입력 |
| Date Picker | `calendar` `popover` | 날짜 (Calendar+Popover 조합) |
| Button | `button` | 액션 (variant: default/destructive/outline/ghost) |

## Feedback & Overlay
| 컴포넌트 | add name | 용도 |
|---|---|---|
| Dialog | `dialog` | 일반 모달 |
| Alert Dialog | `alert-dialog` | 파괴적 확인 (삭제 등) |
| Sonner (Toast) | `sonner` | 알림 토스트 |
| Alert | `alert` | 인라인 경고/정보 |
| Tooltip | `tooltip` | 힌트 |
| Popover / Dropdown Menu | `popover` `dropdown-menu` | 팝오버/컨텍스트 메뉴 |

## Navigation
| 컴포넌트 | add name | 용도 |
|---|---|---|
| Breadcrumb | `breadcrumb` | 경로 표시 |
| Navigation Menu | `navigation-menu` | 상단 네비 |
| Pagination | `pagination` | 페이지네이션 (TanStack Table과 연동) |
| Tabs | `tabs` | 탭 (`TabsTrigger`는 `TabsList` 안에 중첩 필수) |

## Chat / Messaging (AI 스트리밍 — 선택 프리미티브)
| 컴포넌트 | add name | 용도 |
|---|---|---|
| MessageScroller | `message-scroller` | 채팅 스크롤 컨테이너 (자동 하단 고정/위치 복원/jump-to-latest). raw overflow 컨테이너·`ScrollArea` 수동 배선 대신 |
| Message / Bubble | `message` `bubble` | 메시지 행(아바타/헤더/본문/푸터) + 말풍선 표면·리액션 |

> **채팅은 하네스 계약 우선**: 본문은 `react-markdown`(`MarkdownContent`), 스트리밍은 `useAIStreaming` SSE 계약이 SSOT다(`[J]` 게이트). MessageScroller/Message/Bubble은 **프레젠테이션 쉘로만 선택 채택**하고 데이터/본문 렌더는 하네스 계약을 유지한다 — 상세는 `references/ai-streaming.md`.

> 전체 목록·props·접근성은 `npx shadcn@latest view @shadcn/{name}`(소스) / `npx shadcn@latest docs {name}`(예제 URL)로 그 시점에 조회. WebFetch `https://ui.shadcn.com/docs/components/{name}`도 가능(온디맨드, 정독 금지).

> **컴포넌트 vs 수기 마크업 (공식 규칙)**: 콜아웃=`Alert`(+`AlertTitle`/`AlertDescription`), 빈 상태=`Empty`, 토스트=`sonner`, 구분선=`Separator`(`<hr>` 금지), 로딩=`Skeleton`/`Spinner`, 상태=`Badge`. 오버레이는 상황별로 — `Dialog`(집중 작업)/`AlertDialog`(파괴적)/`Sheet`(사이드)/`Drawer`(모바일 하단)/`HoverCard`(호버)/`Popover`(클릭). **`DialogTitle`/`SheetTitle`/`DrawerTitle`은 필수**(숨길 땐 `className="sr-only"`).
