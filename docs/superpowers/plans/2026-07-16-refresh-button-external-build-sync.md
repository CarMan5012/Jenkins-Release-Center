# Refresh Button and External Build Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execute inline because this workspace has no Git metadata; the user explicitly requested manual functional verification instead of automated tests.

**Goal:** Give every data refresh/sync button isolated, visible loading feedback and remove only stale external Jenkins build histories during synchronization.

**Architecture:** Reuse one small Vue button component for consistent spinner rendering while each page keeps its own request state. Add one lightweight Jenkins build-number inventory call, then reconcile only external history rows after a successful inventory response.

**Tech Stack:** Vue 3, TypeScript, Naive UI, FastAPI service layer, SQLAlchemy, requests.

---

### Task 1: Shared refresh button and isolated page state

**Files:**
- Create: `frontend/src/components/RefreshButton.vue`
- Modify: `frontend/src/views/dashboard/index.vue`
- Modify: `frontend/src/views/release/index.vue`
- Modify: `frontend/src/views/release/detail.vue`
- Modify: `frontend/src/views/history/index.vue`
- Modify: `frontend/src/views/jenkins/index.vue`
- Modify: `frontend/src/components/LogViewer.vue`

- [ ] **Step 1: Add the shared button component**

Create a Naive UI wrapper with this behavior:

```vue
<template>
  <n-button
    v-bind="$attrs"
    class="refresh-button"
    :disabled="disabled || loading"
    :aria-busy="loading"
    :aria-label="label"
  >
    <span :class="{ 'refresh-button__label--loading': loading }">{{ label }}</span>
    <span v-if="loading" class="refresh-button__spinner" aria-hidden="true">
      <n-spin :size="12" />
    </span>
  </n-button>
</template>

<script setup lang="ts">
import { NButton, NSpin } from 'naive-ui';

defineOptions({ inheritAttrs: false });
withDefaults(defineProps<{ label: string; loading?: boolean; disabled?: boolean }>(), {
  loading: false,
  disabled: false,
});
</script>

<style scoped>
.refresh-button { position: relative; }
.refresh-button__label--loading { visibility: hidden; }
.refresh-button__spinner {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: inherit;
  pointer-events: none;
}
.refresh-button__spinner :deep(.n-spin) { --n-color: currentColor !important; }
</style>
```

- [ ] **Step 2: Replace refresh/sync markup**

Import `RefreshButton` and replace inline `NButton` + `NSpin` loading markup for dashboard refreshes, release-list refresh, release-detail history refresh and task sync, history-page external sync/header refresh/query, Jenkins server sync/job refresh, and the log viewer manual refresh.

Use labels matching the current UI, for example:

```vue
<RefreshButton type="primary" label="刷新" :loading="btnRefreshLoading" @click="loadDashboard('refresh')" />
<RefreshButton secondary label="刷新列表" :loading="btnListRefreshLoading" @click="loadDashboard('list')" />
```

- [ ] **Step 3: Reset only the state started by the current request**

In `loadDashboard`, replace unconditional clearing of both button flags with trigger-specific clearing:

```ts
if (trigger === 'refresh') btnRefreshLoading.value = false;
else if (trigger === 'list') btnListRefreshLoading.value = false;
else if (trigger !== 'poll') pageLoading.value = false;
```

In the history page, split header refresh and query into separate refs and trigger names:

```ts
const headerRefreshLoading = ref(false);
const queryLoading = ref(false);

async function fetchHistories(resetPage = false, trigger?: 'page' | 'header' | 'query') {
  const buttonLoading = trigger === 'header'
    ? headerRefreshLoading
    : trigger === 'query'
      ? queryLoading
      : null;
  if (buttonLoading) buttonLoading.value = true;
  else pageLoading.value = true;
  // existing request body
  if (buttonLoading) buttonLoading.value = false;
  else pageLoading.value = false;
}
```

Pass `header` from the page-header button and `query` from the toolbar button/Enter key. Keep external sync on its existing independent `syncing` ref.

- [ ] **Step 4: Separate manual log refresh from automatic polling**

Make `restart` return the first `pollLogs` promise and add a manual wrapper with the existing 500ms minimum feedback:

```ts
const manualRefreshing = ref(false);

async function restart() {
  clearPoller();
  offset = 0;
  logBuffer.value = '';
  hasMore.value = false;
  if (props.taskId || props.historyId) await pollLogs();
}

async function refresh() {
  manualRefreshing.value = true;
  const startedAt = Date.now();
  await restart();
  const remaining = 500 - (Date.now() - startedAt);
  if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
  manualRefreshing.value = false;
}
```

Bind only the manual button to `manualRefreshing`; automatic polling continues silently.

### Task 2: Safe external-build deletion reconciliation

**Files:**
- Modify: `backend/app/services/jenkins_client.py`
- Modify: `backend/app/services/jenkins_sync_task.py`

- [ ] **Step 1: Add a lightweight complete build-number query**

Add this method beside `get_recent_builds`:

```py
def get_build_numbers(self, job_name: str) -> set[int] | None:
    job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
    url = urljoin(self.base_url, f"{job_path}/api/json?tree=builds[number]")
    try:
        response = self.session.get(url, timeout=10)
        if response.status_code == 200:
            return {
                build["number"]
                for build in response.json().get("builds", [])
                if isinstance(build.get("number"), int)
            }
        logger.warning(f"Failed to fetch build numbers for job {job_name}: HTTP {response.status_code}")
    except Exception as exc:
        logger.warning(f"Failed to fetch build numbers for job {job_name}: {str(exc)}")
    return None
```

`None` means inventory failure; an empty set means Jenkins successfully reported no builds.

- [ ] **Step 2: Reconcile only external rows after successful inventory**

For each Job, fetch `remote_build_numbers` before processing recent builds. After additions, delete stale rows with all three ownership filters:

```py
if remote_build_numbers is not None:
    stale = db.query(ReleaseHistory).filter(
        ReleaseHistory.server_name == server.name,
        ReleaseHistory.job_name == job.name,
        ReleaseHistory.is_external.is_(True),
    )
    if remote_build_numbers:
        stale = stale.filter(ReleaseHistory.build_number.notin_(remote_build_numbers))
    deleted = stale.delete(synchronize_session=False)
    db.commit()
    if deleted:
        logger.info(f"Removed {deleted} stale external builds for {job.name} on '{server.name}'")
```

Wrap each Job body in `try/except`, call `db.rollback()` on failure, and continue with the next Job. Never delete when the inventory result is `None`.

### Task 3: Build and syntax verification

**Files:**
- No source changes.

- [ ] **Step 1: Verify the frontend builds**

Run:

```powershell
npm run build
```

Working directory: `frontend`. Expected: exit code 0 from `vue-tsc -b` and `vite build`.

- [ ] **Step 2: Verify changed Python modules compile**

Run:

```powershell
backend\venv\Scripts\python.exe -m py_compile backend\app\services\jenkins_client.py backend\app\services\jenkins_sync_task.py
```

Working directory: repository root. Expected: exit code 0 and no output.

- [ ] **Step 3: Hand off the manual checks**

Ask the user to verify:

1. Only the clicked refresh/query/sync button spins.
2. Text disappears without changing button width; the 12px spinner is visible on primary and secondary backgrounds.
3. Very fast refreshes still show feedback.
4. Deleting an external Jenkins build then syncing removes only its external history row.
5. System release history remains intact.
6. Jenkins inventory failure does not remove local rows.
