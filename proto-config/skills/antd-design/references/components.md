# antd v6 컴포넌트 카탈로그

상세 API는 `https://ant.design/components/{name}.md`. 여기는 "무엇을 쓸지"와 v6 규칙만 둔다.

## 용도별 카탈로그

| 용도 | 컴포넌트 | 메모 |
|---|---|---|
| 앱 셸 | `Layout`(`Header`/`Sider`/`Content`/`Footer`), `Menu` | Sider는 `collapsible`, Menu는 `items` |
| 내비게이션 | `Breadcrumb`(`items`), `Tabs`(`items`), `Steps`(`items`), `Dropdown`(`menu`), `Pagination` | 전부 `items` 배열이 정본 |
| 레이아웃 | `Row`/`Col`(24칸, `gutter`), `Flex`, `Space`, `Divider`, `Splitter` | 간격은 `gap`/`gutter` — margin 수기 지정 금지 |
| 데이터 표시 | `Table`, `Descriptions`(`items`), `List`, `Card`, `Statistic`, `Tag`, `Badge`, `Timeline`(`items`), `Tree`, `Avatar`, `Tooltip`, `Popover`, `Image` | 상태 표시는 `Tag`/`Badge`, 커스텀 span 금지 |
| 입력 | `Form`, `Input`(`Input.Search`/`Input.TextArea`/`Input.Password`), `InputNumber`, `Select`, `TreeSelect`, `Cascader`, `AutoComplete`, `DatePicker`(`RangePicker`), `TimePicker`, `Checkbox`, `Radio`, `Switch`, `Slider`, `Upload`, `Transfer`, `Segmented` | 선택지는 `options` prop |
| 피드백 | `App.useApp()`의 `message`/`modal`/`notification`, `Modal`, `Drawer`, `Popconfirm`, `Alert`, `Result`, `Progress`, `Spin`, `Skeleton`, `Empty` | 정적 메서드 대신 `App.useApp()` |
| 텍스트 | `Typography.Title`/`Text`/`Paragraph`/`Link` | 보조 문구는 `type="secondary"` |

## 사용 규칙

- **수기 마크업 대신 컴포넌트**: 구분선→`Divider`, 상태 span→`Tag`, 빈 결과→`Empty`, 로딩→`Skeleton`/`Spin`/`Table loading`/`Button loading`, 안내 박스→`Alert`, 오류 페이지→`Result`, 토스트→`message`.
- **파괴적 작업은 확인**: 행 삭제는 `Popconfirm`, 일괄 삭제·되돌릴 수 없는 작업은 `modal.confirm({ okButtonProps: { danger: true } })`.
- **`Modal`/`Drawer`는 `title` 필수**, 닫힐 때 상태를 버리려면 `destroyOnHidden`.
- **`Table`은 `rowKey` 필수**(`rowKey="id"`) — 없으면 선택·정렬이 행을 잘못 짚는다.
- **아이콘만 있는 `Button`에는 `aria-label`** 또는 `Tooltip`.
- **커스텀 스타일**은 `theme.useToken()`의 토큰 값으로, 컴포넌트 내부는 `classNames`/`styles` 슬롯(`https://ant.design/components/{name}/semantic.md`)으로.

## v6 prop 이름 변경표 (자주 쓰는 것)

옛 이름은 v6에서 동작하지만 콘솔 경고를 내고 v7에서 제거된다. **새 이름으로 쓴다.** 전체 목록: `https://ant.design/docs/react/migration-v6`.

| 컴포넌트 | 옛 이름 | v6 이름 |
|---|---|---|
| Alert | `message` | `title` |
| Alert | `closeText`/`closeIcon`/`onClose` | `closable={{ closeIcon, onClose }}` |
| Breadcrumb | `Breadcrumb.Item`, `routes` | `items` |
| Button | `iconPosition` | `iconPlacement` |
| Button.Group | `Button.Group` | `Space.Compact` |
| Card | `bordered` | `variant` |
| Card | `headStyle`/`bodyStyle` | `styles={{ header, body }}` |
| Descriptions | `children`(`Descriptions.Item`) | `items` |
| Descriptions | `size="default"`/`"middle"` | `"large"`/`"medium"` |
| Divider | `type` | `orientation` |
| Drawer | `width`/`height` | `size` |
| Drawer / Modal | `destroyOnClose` | `destroyOnHidden` |
| Drawer / Modal | `maskClosable` | `mask={{ closable }}` |
| Drawer / Modal | `bodyStyle`/`maskStyle` | `styles={{ body, mask }}` |
| Dropdown | `overlay`/`dropdownRender` | `menu` / `popupRender` |
| Dropdown.Button | `Dropdown.Button` | `Space.Compact` + `Dropdown` + `Button` |
| Input / Select / DatePicker 등 | `bordered` | `variant` |
| Input.Group | `Input.Group` | `Space.Compact` |
| InputNumber | `addonBefore`/`addonAfter` | `Space.Compact` |
| Menu | `children`(`Menu.Item`) | `items` |
| notification | `message`/`btn` | `title`/`actions` |
| Progress | `strokeWidth`/`width` | `size` |
| Select 등 팝업 | `dropdownRender`/`dropdownStyle`/`onDropdownVisibleChange` | `popupRender`/`styles.popup.root`/`onOpenChange` |
| Space | `direction` | `orientation` |
| Space | `split` | `separator` |
| Spin | `tip` | `description` |
| Statistic | `valueStyle` | `styles={{ content }}` |
| Statistic.Countdown | `<Statistic.Countdown />` | `<Statistic.Timer type="countdown" />` |
| Steps | `direction` | `orientation` |
| Steps | `items[].description` | `items[].content` |
| Steps | `progressDot` | `type="dot"` |
| Table | `size="middle"` | `size="medium"` |
| Table | `pagination.position` | `pagination.placement` |
| Tabs | `Tabs.TabPane` | `items` |
| Tabs | `tabPosition` | `tabPlacement` |
| Tag | `bordered={false}` | `variant="filled"` |
| Timeline | `Timeline.Item` | `items` |
| Timeline | `items[].label`/`children`/`dot` | `items[].title`/`content`/`icon` |
| Tooltip | `overlayStyle`/`overlayClassName` | `styles.root`/`classNames.root` |
| Avatar / Badge / Spin / Steps | `size="default"` | `size="medium"` |

## v6에서 달라진 동작

- **CSS 변수 모드가 기본**이다. 테마 토큰은 `--ant-*` 변수로 나가지만, 값은 여전히 `ConfigProvider theme.token`에서 정한다(변수를 직접 덮어쓰지 않는다 — 파생 색이 따라오지 않는다).
- **DOM 구조가 바뀐 컴포넌트가 많다** — `.ant-*` 내부 클래스를 CSS로 겨냥하지 말고 `classNames`/`styles` 슬롯을 쓴다.
- `Tag`의 오른쪽 기본 여백이 없어졌다 — 여러 개를 나열하면 `Space`/`Flex`로 감싼다.
- `Form.List`의 `onFinish` 값에 등록되지 않은 하위 항목이 들어가지 않는다.
