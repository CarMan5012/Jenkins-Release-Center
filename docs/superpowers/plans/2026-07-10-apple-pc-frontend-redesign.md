# Apple 风格 PC 前端重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变业务逻辑和接口的前提下，将 Jenkins 发布中心重构为 Apple 桌面应用风格的 PC Web，并清理确认无用的前端代码。

**Architecture:** 保留 Vue 3、Naive UI、Pinia 和现有页面结构，以 `style.css` 与 `App.vue` 作为唯一全局视觉令牌入口，各页面只保留自身布局样式。重构按“基线验证 → 全局主题 → 应用框架 → 业务页面 → 死代码清理 → 全量验证”推进，不引入新依赖或新抽象层。

**Tech Stack:** Vue 3、TypeScript、Vite、Naive UI、Pinia、Vue Router、Node.js assert

---

## 文件职责

- `frontend/src/App.vue`：Naive UI 全局主题覆盖。
- `frontend/src/style.css`：颜色、字体、间距、圆角、阴影及共享表格、面板、工具栏、日志样式。
- `frontend/src/views/MainLayout.vue`：PC 侧栏、顶栏和内容框架。
- `frontend/src/views/login/index.vue`：桌面登录页。
- `frontend/src/views/dashboard/index.vue`：概览、任务表、Jenkins 健康和最近执行。
- `frontend/src/views/jenkins/index.vue`：实例、View、Job 和相关弹窗。
- `frontend/src/views/release/index.vue`：计划列表与创建/编辑向导。
- `frontend/src/views/release/detail.vue`：计划摘要、任务、历史和日志。
- `frontend/src/views/history/index.vue`：执行历史。
- `frontend/src/views/config/index.vue`：通知渠道和审计日志。
- `frontend/src/components/StatusBadge.vue`：业务状态展示，保留现有公共接口。
- `frontend/src/components/LogViewer.vue`：日志查看器，保留现有 props 与事件。
- `frontend/index.html`、`frontend/src/main.ts`：移除外部字体请求，使用本地系统字体栈。
- `frontend/scripts/release-ui.test.mjs`：现有公共业务接缝回归测试。

### Task 1：建立可验证基线

**Files:**
- Test: `frontend/scripts/release-ui.test.mjs`
- Verify: `frontend/package.json`

- [ ] **Step 1：运行现有公共业务接缝测试**

Run:

```powershell
npm.cmd run test:ui
```

Expected: 进程退出码为 `0`；状态映射、时长格式化、计划筛选和排序断言全部通过。

- [ ] **Step 2：运行重构前生产构建**

Run:

```powershell
npm.cmd run build
```

Expected: `vue-tsc -b` 与 `vite build` 退出码为 `0`。若失败，先记录为基线问题，不把它误判为重构回归。

- [ ] **Step 3：固定测试接缝**

本次只修改视觉和布局，不新增业务逻辑。保留以下公共接缝：

```text
release-ui.test.mjs：状态、格式化、筛选、排序
npm run build：Vue 模板、TypeScript 类型、导入和生产打包
路由级人工检查：登录、仪表盘、Jenkins、发布、详情、历史、配置
```

不新增快照测试或 CSS 实现细节测试，因为它们无法验证用户行为，并会把测试耦合到样式实现。

### Task 2：建立 Apple 风格全局主题

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/index.html`

- [ ] **Step 1：移除外部字体请求**

将 `frontend/index.html` 的字体链接删除，仅保留：

```html
<meta name="description" content="Jenkins 发布调度中心" />
<title>发布调度中心</title>
```

从 `frontend/src/main.ts` 删除：

```ts
import 'vfonts/Lato.css';
```

保留 `FiraCode.css` 作为日志和编号的本地等宽字体资源。

- [ ] **Step 2：替换全局设计令牌**

在 `frontend/src/style.css` 使用以下令牌作为唯一视觉基线：

```css
:root {
  --primary: #0071e3;
  --primary-dark: #0062c3;
  --primary-soft: #e8f2ff;
  --bg: #f5f5f7;
  --surface: rgba(255, 255, 255, 0.92);
  --surface-solid: #ffffff;
  --surface-subtle: #f2f2f4;
  --line: rgba(60, 60, 67, 0.16);
  --line-soft: rgba(60, 60, 67, 0.10);
  --text: #1d1d1f;
  --text-muted: #6e6e73;
  --text-faint: #8e8e93;
  --radius-sm: 8px;
  --radius: 12px;
  --radius-lg: 16px;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.07);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif;
}
```

- [ ] **Step 3：统一共享控件**

将共享页面元素收敛为安静的 Apple 桌面表面：

```css
.panel {
  overflow: hidden;
  background: var(--surface-solid);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.ops-table th {
  background: #fafafa;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  text-transform: none;
}

.ops-table tr:hover td {
  background: #f7f9fc;
}

:focus-visible {
  outline: 3px solid rgba(0, 113, 227, 0.28);
  outline-offset: 2px;
}
```

- [ ] **Step 4：同步 Naive UI 主题**

在 `frontend/src/App.vue` 将主题核心值同步为：

```ts
common: {
  primaryColor: '#0071e3',
  primaryColorHover: '#147ce5',
  primaryColorPressed: '#0062c3',
  bodyColor: '#f5f5f7',
  cardColor: '#ffffff',
  borderColor: 'rgba(60, 60, 67, 0.16)',
  textColorBase: '#1d1d1f',
  textColor2: '#6e6e73',
  borderRadius: '10px',
  fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif',
}
```

- [ ] **Step 5：验证主题阶段**

Run:

```powershell
npm.cmd run test:ui
npm.cmd run build
```

Expected: 两个命令退出码均为 `0`。

### Task 3：重构应用框架与登录页

**Files:**
- Modify: `frontend/src/views/MainLayout.vue`
- Modify: `frontend/src/views/login/index.vue`

- [ ] **Step 1：重构 PC 应用框架**

保留现有导航数组、路由标题、退出逻辑和模板语义，只替换布局样式：

```css
.app-shell {
  min-width: 1024px;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  background: var(--bg);
}

.side-nav {
  position: sticky;
  top: 0;
  height: 100dvh;
  background: rgba(238, 238, 240, 0.82);
  border-right: 1px solid var(--line-soft);
  backdrop-filter: saturate(180%) blur(24px);
}

.nav-link--active {
  color: var(--text);
  background: rgba(0, 113, 227, 0.12);
}

.content {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
  padding: 28px 32px 40px;
}
```

删除 `MainLayout.vue` 中 `@media (max-width: 900px)` 的移动导航转换。

- [ ] **Step 2：重构桌面登录页**

保留现有表单、验证、登录请求和错误状态，将布局改为固定桌面画布与聚焦登录面板：

```css
.login-shell {
  min-width: 1024px;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  background: #f5f5f7;
}

.login-panel {
  width: 420px;
  padding: 40px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line-soft);
  border-radius: 20px;
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(24px);
}
```

- [ ] **Step 3：验证框架阶段**

Run:

```powershell
npm.cmd run build
```

Expected: 退出码为 `0`，路由组件和模板编译成功。

### Task 4：重构数据密集型页面

**Files:**
- Modify: `frontend/src/views/dashboard/index.vue`
- Modify: `frontend/src/views/jenkins/index.vue`
- Modify: `frontend/src/views/history/index.vue`
- Modify: `frontend/src/views/config/index.vue`

- [ ] **Step 1：重构仪表盘信息层级**

保留所有计算属性和事件，将指标区改为连续概览表面：

```css
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  overflow: hidden;
  background: var(--surface-solid);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
}

.metric-tile {
  border: 0;
  border-right: 1px solid var(--line-soft);
  border-radius: 0;
  box-shadow: none;
}
```

删除仪表盘的移动断点，只保留固定 PC 双栏和四列指标布局。

- [ ] **Step 2：重构 Jenkins 工作区**

实例选择、View 列表和 Job 表格维持原逻辑，使用明确的来源列表与选中态：

```css
.server-card--active {
  border-color: rgba(0, 113, 227, 0.38);
  background: var(--primary-soft);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.08);
}

.workspace-grid {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 16px;
}
```

- [ ] **Step 3：统一历史与配置页面**

保留分页、筛选、通知渠道和审计逻辑，统一工具栏、分组表面和操作层级。危险操作继续使用 Naive UI `error` 语义，普通编辑操作使用 `secondary`。

```css
.pagination-row,
.channel-actions,
.audit-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.channel-card {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  background: #fafafa;
}
```

- [ ] **Step 4：验证数据页阶段**

Run:

```powershell
npm.cmd run test:ui
npm.cmd run build
```

Expected: 两个命令退出码均为 `0`。

### Task 5：重构发布计划、详情与日志

**Files:**
- Modify: `frontend/src/views/release/index.vue`
- Modify: `frontend/src/views/release/detail.vue`
- Modify: `frontend/src/components/LogViewer.vue`
- Modify: `frontend/src/components/StatusBadge.vue`

- [ ] **Step 1：重构计划列表和向导**

不改向导状态、任务依赖、提交和编辑逻辑。使用固定 PC 双列字段和清晰步骤层级：

```css
.task-row__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.summary-box {
  background: #fafafa;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
}
```

删除 `release/index.vue` 的移动断点。

- [ ] **Step 2：重构详情信息顺序**

保留现有模板区块和逻辑，将状态摘要作为首要连续表面，任务和日志占主要宽度：

```css
.detail-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  padding: 0;
}

.detail-summary > div {
  padding: 18px 20px;
  border-right: 1px solid var(--line-soft);
}
```

删除详情页移动断点。

- [ ] **Step 3：完善日志与状态组件**

保持 `LogViewer` props、轮询、复制、滚动和行分类逻辑不变；移除模板内联样式并使用类：

```css
.log-viewer__build {
  margin-left: 4px;
  font-size: 12px;
}

.log-console {
  background: #111113;
  border-color: rgba(255, 255, 255, 0.08);
  border-radius: var(--radius);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}
```

状态徽标继续通过 `getStatusMeta()` 映射，不复制状态判断逻辑。

- [ ] **Step 4：验证发布页阶段**

Run:

```powershell
npm.cmd run test:ui
npm.cmd run build
```

Expected: 两个命令退出码均为 `0`。

### Task 6：删除确认无用的代码和资源

**Files:**
- Delete: `frontend/src/components/HelloWorld.vue`
- Delete: `frontend/src/assets/vue.svg`
- Delete: `frontend/src/assets/vite.svg`
- Delete: `frontend/src/assets/hero.png`
- Delete: `frontend/public/icons.svg`
- Review: `frontend/src/**/*.vue`
- Review: `frontend/src/style.css`

- [ ] **Step 1：再次证明资源无引用**

Run:

```powershell
rg -n "HelloWorld|vue\.svg|vite\.svg|hero\.png|icons\.svg" frontend/src frontend/index.html frontend/public
```

Expected: 命中只存在于待删除的 `HelloWorld.vue`、默认资源本身和 `icons.svg`；生产入口、路由和业务组件无引用。

- [ ] **Step 2：删除默认示例资源**

删除上述五个确认无引用的文件。保留 `frontend/public/favicon.svg`，因为浏览器入口仍需要品牌 favicon；若 `index.html` 尚未引用，则添加：

```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
```

- [ ] **Step 3：清理移动样式和死 CSS**

Run:

```powershell
rg -n "@media|max-width" frontend/src
```

Expected: 不再存在为手机或平板重排页面的媒体查询。允许与打印、减少动画偏好等非移动用途相关的媒体查询。

- [ ] **Step 4：验证清理结果**

Run:

```powershell
npm.cmd run test:ui
npm.cmd run build
```

Expected: 两个命令退出码均为 `0`，且构建输出无缺失资源或导入错误。

### Task 7：全量验证与 Matt Pocock 代码审查

**Files:**
- Review: `frontend/src/**`
- Review: `frontend/index.html`
- Spec: `docs/superpowers/specs/2026-07-10-apple-pc-frontend-redesign-design.zh-CN.md`

- [ ] **Step 1：运行最终自动验证**

Run:

```powershell
npm.cmd run test:ui
npm.cmd run build
```

Expected: 两个命令均为本轮新鲜运行，退出码为 `0`。

- [ ] **Step 2：运行无用代码与移动样式检查**

Run:

```powershell
rg -n "HelloWorld|vue\.svg|vite\.svg|hero\.png|icons\.svg|@media.*max-width" frontend/src frontend/index.html frontend/public
```

Expected: 无命中。

- [ ] **Step 3：按 `code-review` 做标准与规格双轴审查**

当前目录不是有效 Git 仓库，无法使用该 skill 默认的 `git diff <fixed-point>...HEAD`。以设计文档为规格来源，改用本次实际修改文件清单进行等价审查：

```text
Standards：检查重复样式、推测性抽象、神秘命名、无用导入、内联样式和现有技术栈一致性。
Spec：逐条核对 Apple 桌面风格、仅 PC、业务不变、无新依赖、死代码删除和全部页面覆盖。
```

- [ ] **Step 4：人工桌面路由检查**

在至少 1440×900 的浏览器视口检查：

```text
/login
/dashboard
/jenkins
/release
/release/:id
/history
/config
```

确认导航、登录、筛选、表格、弹窗、创建/编辑计划、危险操作禁用态、加载、空数据、错误和日志展示均正常。

- [ ] **Step 5：提交限制说明**

`implement` skill 默认要求提交当前分支，但当前目录没有可解析的 Git 仓库。不得擅自执行 `git init`；交付时明确报告未提交原因和实际修改文件。
