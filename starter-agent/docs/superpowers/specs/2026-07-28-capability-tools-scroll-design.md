# 能力管理 Tools 列表滚动设计

## 背景与目标

能力管理的 MCP Server 详情会直接渲染全部 Tool。Playwright 当前包含 24 个 Tool，导致详情页纵向过长，Tools 后方的 Resources、Prompts 与 Tool 详情难以快速到达。

目标是在不改变后端 API、Tool 状态、选择逻辑和详情加载行为的前提下，仅限制 Tool 卡片列表的可视高度，并在内容溢出时提供纵向滚动。

## 设计

在现有 `Tools (N)` 标题下增加专用列表容器 `.capability-tools-list`。所有 Tool 按钮以及空状态均渲染到这个容器，标题本身保持在滚动区域之外。

桌面端列表使用：

- `max-height: min(52vh, 560px)`；
- `overflow-y: auto`；
- `overscroll-behavior: contain`，避免滚动到边界后立即带动整个页面；
- `scrollbar-gutter: stable`，避免滚动条出现时卡片宽度跳动；
- 保留现有卡片间距，并为滚动条预留少量右侧空间。

在现有窄屏断点中，将最大高度覆盖为 `420px`。列表不足最大高度时维持内容自然高度，不出现多余空白。

## 交互与可访问性

- Tool 按钮仍使用现有 `capabilityButton`，点击、禁用状态与 Tool 详情加载逻辑不变。
- 列表容器使用可读的 `aria-label="Server tools"`，不额外引入自定义滚动事件。
- 鼠标滚轮、触控板、触屏和键盘聚焦后的浏览器原生滚动均由标准 overflow 行为处理。
- `Tools (N)` 始终可见，便于用户确认总数。

## 边界与非目标

- Tool 为 0 时，空状态显示在列表容器内，但容器不会撑到最大高度。
- 长 Tool 名继续沿用现有 `overflow-wrap: anywhere`。
- 本次不增加分页、搜索、虚拟列表、固定域名逻辑或后端字段。
- Resources、Prompts、Tool Schema 和左侧 MCP Server 列表不改变滚动方式。

## 测试与验收

新增 UI 合约测试，验证：

1. 页面存在 `.capability-tools-list` 的最大高度、纵向滚动、滚动边界与稳定 gutter 样式。
2. 窄屏断点包含 `420px` 最大高度覆盖。
3. Server 详情渲染会创建带 `aria-label` 的专用容器，并将 Tool 按钮追加到该容器，而不是直接追加到 subsection。
4. 现有能力管理、Tool 确认卡与 Trust Center UI 合约测试继续通过。

人工验收时打开含 24 个 Tool 的 Playwright Server：Tools 标题保持可见，列表内部可上下滚动，页面不再被全部 Tool 卡片拉长；桌面和窄屏均可点击任意 Tool 查看详情。
