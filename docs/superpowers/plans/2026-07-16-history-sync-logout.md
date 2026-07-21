# History Sync and Logout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place the external-build sync action with its owning Tab and replace the account chip with a confirmed icon-and-text logout button.

**Architecture:** Reuse Naive UI's existing Tabs suffix slot, dialog provider, icon component, and the installed Ionicons package. Keep the existing sync and logout request functions; only change their presentation and add a confirmation boundary before logout.

**Tech Stack:** Vue 3, TypeScript, Naive UI, `@vicons/ionicons5`.

---

### Task 1: Move the external sync action into the Tab row

**Files:**
- Modify: `frontend/src/views/history/index.vue:3-29`

- [ ] **Step 1: Keep only the global refresh in the page header**

Remove the existing conditional external sync button from `.page-header`; retain:

```vue
<RefreshButton secondary label="刷新" :loading="headerRefreshLoading" @click="fetchHistories(true, 'header')" />
```

- [ ] **Step 2: Add the Tab-specific action through the existing suffix slot**

Change the Tabs block to:

```vue
<n-tabs v-model:value="activeTab" type="line" animated style="margin-bottom: 16px" @update:value="onTabChange">
  <n-tab-pane name="system" tab="系统发版历史" />
  <n-tab-pane name="external" tab="Jenkins 外部手动构建" />
  <template #suffix>
    <RefreshButton
      v-if="activeTab === 'external'"
      size="small"
      type="primary"
      label="同步外部构建"
      :loading="syncing"
      @click="syncExternalHistories"
    />
  </template>
</n-tabs>
```

No script or style changes are needed in this file.

### Task 2: Replace the account chip with confirmed logout

**Files:**
- Modify: `frontend/src/views/MainLayout.vue:28-34`
- Modify: `frontend/src/views/MainLayout.vue:45-64`
- Modify: `frontend/src/views/MainLayout.vue:86-95`
- Modify: `frontend/src/views/MainLayout.vue:229-273`

- [ ] **Step 1: Render an icon-and-text logout button**

Replace the account chip with:

```vue
<button class="logout-button" type="button" @click="confirmLogout">
  <n-icon :component="LogOutOutline" aria-hidden="true" />
  <span>退出</span>
</button>
```

- [ ] **Step 2: Reuse installed dialog and icon dependencies**

Update imports and setup:

```ts
import { NIcon, useDialog } from 'naive-ui';
import {
  BuildOutline,
  FileTrayFullOutline,
  GitNetworkOutline,
  HomeOutline,
  LogOutOutline,
  SettingsOutline,
} from '@vicons/ionicons5';

const dialog = useDialog();
```

Keep `authStore` because `handleLogout` still clears local authentication state.

- [ ] **Step 3: Add the confirmation boundary**

Add this function before `handleLogout`:

```ts
function confirmLogout() {
  dialog.warning({
    title: '退出登录',
    content: '确认退出当前账号？',
    positiveText: '退出',
    negativeText: '取消',
    onPositiveClick: handleLogout,
  });
}
```

The existing `handleLogout` request, store cleanup, and login redirect stay unchanged.

- [ ] **Step 4: Replace account-only styles**

Delete `.account-chip`, `.account-chip__avatar`, and `.account-chip__name`. Add:

```css
.logout-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.82);
  color: var(--text-muted);
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.logout-button :deep(.n-icon) {
  font-size: 16px;
}

.logout-button:hover {
  border-color: var(--line);
  background: #fff;
  color: var(--text);
}

.logout-button:active {
  transform: scale(0.98);
}

.logout-button:focus-visible {
  outline: 2px solid rgba(0, 113, 227, 0.35);
  outline-offset: 2px;
}
```

### Task 3: Build verification and manual handoff

**Files:**
- No source changes.

- [ ] **Step 1: Run the frontend build**

Run from `frontend`:

```powershell
npm.cmd run build
```

Expected: `vue-tsc -b` and `vite build` exit with code 0.

- [ ] **Step 2: Hand off manual verification**

Ask the user to confirm:

1. External sync appears at the right side of the external-build Tab row only.
2. The page header retains only the global refresh action.
3. The top-right control shows the logout icon and “退出”, with no avatar or username.
4. Clicking logout opens the confirmation dialog; cancel does nothing and confirm logs out.
