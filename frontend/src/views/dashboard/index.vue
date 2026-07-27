<template>
  <section class="page dashboard-page">
    <div class="page-header">
      <div>
        <h1>仪表盘</h1>
        <p>发布任务、Jenkins 健康和最近执行状态。</p>
      </div>
      <RefreshButton type="primary" label="刷新" :loading="btnRefreshLoading" @click="loadDashboard('refresh')" />
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

    <div class="metrics-grid">
      <div v-for="metric in metrics" :key="metric.label" class="metric-tile panel">
        <span>{{ metric.label }}</span>
        <strong>{{ metric.value }}</strong>
        <small>{{ metric.note }}</small>
      </div>
    </div>

    <div class="toolbar">
      <n-input v-model:value="keyword" clearable placeholder="搜索任务名称" style="width: 260px" />
      <n-select v-model:value="statusFilter" :options="statusOptions" style="width: 150px" />
      <n-select v-model:value="typeFilter" :options="typeOptions" style="width: 160px" />
      <n-select v-model:value="sortKey" :options="sortOptions" style="width: 170px" />
      <RefreshButton secondary label="刷新列表" :loading="btnListRefreshLoading" @click="loadDashboard('list')" />
    </div>

    <section class="panel">
      <div class="panel__header">
        <h2 class="panel__title">发布任务列表</h2>
        <span class="muted mono">{{ visiblePlans.length }} / {{ plans.length }}</span>
      </div>

      <div v-if="pageLoading" class="skeleton-block">
        <n-skeleton text :repeat="8" />
      </div>

      <div v-else class="table-wrap">
        <table class="ops-table">
          <thead>
            <tr>
              <th scope="col">名称</th>
              <th scope="col">状态</th>
              <th scope="col">最近运行</th>
              <th scope="col">星期</th>
              <th scope="col">耗时</th>
              <th scope="col">负责人/来源</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="plan in visiblePlans" :key="plan.id">
              <td>
                <RouterLink class="cell-title" :to="`/release/${plan.id}`">
                  <strong>{{ plan.name }}</strong>
                </RouterLink>
              </td>
              <td>
                <div class="cell-title">
                  <StatusBadge :status="plan.status" />
                  <span>
                    <n-tag size="small" :type="preflightTagType(plan.preflight_status)">{{ getPreflightMeta(plan.preflight_status).label }}</n-tag>
                  </span>
                </div>
              </td>
              <td class="mono">{{ formatDateTime(plan.execute_time || plan.created_at) }}</td>
              <td class="mono">{{ getWeekDay(plan.execute_time || plan.created_at) }}</td>
              <td class="mono">{{ formatDuration(getPlanDurationSeconds(plan)) }}</td>
              <td>
                <div class="cell-title">
                  <strong>creator #{{ plan.creator_id }}</strong>
                  <span class="muted">{{ plan.tasks?.[0]?.job_name || 'Jenkins' }}</span>
                </div>
              </td>
              <td>
                <div class="cell-actions">
                  <n-button size="tiny" type="primary" :loading="busyKey === `run-${plan.id}`" :disabled="plan.status !== 'WAITING' || isPreflightBlocked(plan.preflight_status)" :title="isPreflightBlocked(plan.preflight_status) ? '请先完成并通过检测' : undefined" @click="triggerPlan(plan)">运行</n-button>
                  <n-button size="tiny" type="warning" secondary :loading="busyKey === `stop-${plan.id}`" :disabled="!['WAITING', 'RUNNING'].includes(plan.status)" @click="cancelPlan(plan)">停止</n-button>
                  <n-button size="tiny" secondary :disabled="plan.status !== 'WAITING'" @click="editPlan(plan)">编辑</n-button>
                  <n-button size="tiny" secondary :disabled="!plan.tasks?.length" @click="openLogs(plan)">日志</n-button>
                  <n-button size="tiny" type="error" secondary :loading="busyKey === `delete-${plan.id}`" :disabled="plan.status === 'RUNNING'" @click="deletePlan(plan)">删除</n-button>
                </div>
              </td>
            </tr>
            <tr v-if="!visiblePlans.length">
              <td colspan="7"><div class="empty-inline">没有匹配的发布任务。</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="side-grid">
      <section class="panel">
        <div class="panel__header">
          <h2 class="panel__title">Jenkins 健康</h2>
          <span class="muted mono">{{ healthyCount }}/{{ servers.length }}</span>
        </div>
        <div class="health-list">
          <div v-for="server in servers" :key="server.name" class="health-row">
            <span class="cell-title">
              <strong>{{ server.name }}</strong>
              <span class="muted">连接状态</span>
            </span>
            <StatusBadge :status="server.status === 'UP' ? 'SUCCESS' : 'FAILED'" />
          </div>
          <div v-if="!servers.length" class="empty-inline">暂无 Jenkins 实例。</div>
        </div>
      </section>

      <section class="panel">
        <div class="panel__header">
          <h2 class="panel__title">最近执行</h2>
          <span class="muted mono">{{ recentHistory.length }}</span>
        </div>
        <div class="recent-list">
          <div v-for="item in recentHistory" :key="item.id" class="recent-row">
            <div class="cell-title">
              <strong>{{ item.job_name || '-' }}</strong>
              <span class="muted mono">{{ item.branch || '-' }} · #{{ item.build_number || '-' }}</span>
            </div>
            <div class="recent-row__meta">
              <StatusBadge :status="item.status" />
              <span class="muted mono">{{ formatDateTime(item.started_at || item.created_at) }}</span>
            </div>
          </div>
          <div v-if="!recentHistory.length" class="empty-inline">暂无执行记录。</div>
        </div>
      </section>
    </div>

    <n-modal v-model:show="logModalVisible" preset="card" style="width: min(960px, 94vw)" @after-leave="activeTask = null">
      <LogViewer :task-id="activeTask?.id" :title="activeTask?.job_name || 'Console 日志'" :build-number="activeTask?.build_number" />
    </n-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NAlert, NButton, NInput, NModal, NSelect, NSkeleton, NTag, useDialog, useMessage } from 'naive-ui';
import LogViewer from '../../components/LogViewer.vue';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import {
  filterPlans,
  formatDateTime,
  formatDuration,
  getPreflightMeta,
  getPlanDurationSeconds,
  isPreflightBlocked,
  sortPlans,
  getWeekDay,
} from '../../utils/release-ui';
import type { PreflightStatus } from '../../utils/release-ui';

interface ReleaseTask {
  id: number;
  server_id: number;
  job_id: number;
  job_name: string;
  branch: string;
  parameters: Record<string, unknown>;
  sequence: number;
  duration: number;
  status: string;
  build_number?: number | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

interface ReleasePlan {
  id: number;
  name: string;
  type: string;
  execute_time?: string | null;
  interval_minutes: number;
  pipeline_failure_strategy: string;
  status: string;
  creator_id: number;
  created_at: string;
  preflight_status: PreflightStatus;
  preflight_checked_at?: string | null;
  preflight_result?: {
    summary?: string;
    tasks?: Array<{
      task_id: number;
      job_name: string;
      status: PreflightStatus;
      checks: Array<{ code: string; status: PreflightStatus; message: string }>;
    }>;
  } | null;
  tasks: ReleaseTask[];
}

interface RecentHistory {
  id: number;
  job_name?: string;
  branch?: string;
  build_number?: number | null;
  status?: string;
  started_at?: string;
  created_at?: string;
}

const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const pageLoading = ref(false);
const btnRefreshLoading = ref(false);
const btnListRefreshLoading = ref(false);
const error = ref('');
const busyKey = ref('');
const plans = ref<ReleasePlan[]>([]);
const servers = ref<Array<{ name: string; status: string }>>([]);
const recentHistory = ref<RecentHistory[]>([]);
const dashboardStats = ref({ success_rate: 100, failed_releases: 0, running_releases: 0 });

const keyword = ref('');
const statusFilter = ref('ALL');
const typeFilter = ref('ALL');
const sortKey = ref('created_desc');
const logModalVisible = ref(false);
const activeTask = ref<ReleaseTask | null>(null);

const statusOptions = [
  { label: '全部状态', value: 'ALL' },
  { label: '等待中', value: 'WAITING' },
  { label: '运行中', value: 'RUNNING' },
  { label: '成功', value: 'SUCCESS' },
  { label: '失败', value: 'FAILED' },
  { label: '已取消', value: 'CANCELLED' },
];

const typeOptions = [
  { label: '全部类型', value: 'ALL' },
  { label: '立即执行', value: 'IMMEDIATE' },
  { label: '定时执行', value: 'SCHEDULED' },
  { label: '批量调度', value: 'BATCH' },
  { label: '流水线串行', value: 'PIPELINE' },
];

const sortOptions = [
  { label: '创建时间倒序', value: 'created_desc' },
  { label: '执行时间正序', value: 'execute_asc' },
  { label: '名称 A-Z', value: 'name_asc' },
  { label: '状态 A-Z', value: 'status_asc' },
];

const visiblePlans = computed(() => sortPlans(filterPlans(plans.value, keyword.value, statusFilter.value, typeFilter.value), sortKey.value));
const totalTasks = computed(() => plans.value.reduce((sum, plan) => sum + (plan.tasks?.length || 0), 0));
const runningTasks = computed(() => plans.value.filter((plan) => plan.status === 'RUNNING').length);
const healthyCount = computed(() => servers.value.filter((server) => server.status === 'UP').length);
const latestRun = computed(() => {
  const latest = recentHistory.value[0];
  return latest ? formatDateTime(latest.started_at || latest.created_at) : '-';
});
const metrics = computed(() => [
  { label: '总任务数', value: totalTasks.value, note: `${plans.value.length} 个发布计划` },
  { label: '运行中', value: runningTasks.value, note: `${dashboardStats.value.running_releases || 0} 个后端运行项` },
  { label: '成功率', value: `${dashboardStats.value.success_rate}%`, note: '历史执行统计' },
  { label: '失败数', value: dashboardStats.value.failed_releases, note: '历史失败记录' },
  { label: '最近执行', value: latestRun.value, note: '按历史记录排序' },
]);

function preflightTagType(status: PreflightStatus): 'default' | 'success' | 'warning' | 'error' {
  const tone = getPreflightMeta(status).tone;
  if (tone === 'success' || tone === 'warning') return tone;
  return tone === 'danger' ? 'error' : 'default';
}

async function loadDashboard(trigger?: 'page' | 'refresh' | 'list' | 'poll') {
  if (trigger === 'refresh') {
    btnRefreshLoading.value = true;
  } else if (trigger === 'list') {
    btnListRefreshLoading.value = true;
  } else if (trigger === 'poll') {
    // Silent update, no loading indicator to maintain smooth user experience
  } else {
    pageLoading.value = true;
  }
  
  const startTime = Date.now();
  error.value = '';
  try {
    const [statsRes, plansRes] = await Promise.all([
      request.get('/system/dashboard/stats'),
      request.get('/release/plans'),
    ]);
    dashboardStats.value = statsRes.data.stats || dashboardStats.value;
    servers.value = statsRes.data.servers || [];
    recentHistory.value = statsRes.data.recent_history || [];
    plans.value = plansRes.data || [];
  } catch (err: any) {
    error.value = err.message || 'Dashboard 加载失败。';
  } finally {
    const elapsed = Date.now() - startTime;
    if (elapsed < 500) {
      await new Promise((resolve) => setTimeout(resolve, 500 - elapsed));
    }
    if (trigger === 'refresh') btnRefreshLoading.value = false;
    else if (trigger === 'list') btnListRefreshLoading.value = false;
    else if (trigger !== 'poll') pageLoading.value = false;
  }
}

async function runAction(key: string, action: () => Promise<void>) {
  busyKey.value = key;
  try {
    await action();
    await loadDashboard();
  } catch (err: any) {
    message.error(err.message || '操作失败。');
  } finally {
    busyKey.value = '';
  }
}

function triggerPlan(plan: ReleasePlan) {
  if (isPreflightBlocked(plan.preflight_status)) {
    message.warning('请先完成并通过检测。');
    return;
  }
  const execute = () => runAction(`run-${plan.id}`, async () => {
    await request.post(`/release/plans/${plan.id}/trigger`);
    message.success('已触发执行。');
  });
  if (plan.preflight_status === 'WARNING') {
    dialog.warning({
      title: '检测存在警告',
      content: `计划 [${plan.name}] 检测存在警告，是否现在立即运行？`,
      positiveText: '仍然运行',
      negativeText: '取消',
      onPositiveClick: execute,
    });
    return;
  }
  dialog.info({
    title: '运行确认',
    content: `是否现在立即运行发布计划 [${plan.name}]？`,
    positiveText: '立即运行',
    negativeText: '取消',
    onPositiveClick: execute,
  });
}

function cancelPlan(plan: ReleasePlan) {
  dialog.warning({
    title: '停止发布任务',
    content: `确认停止 ${plan.name}？`,
    positiveText: '停止',
    negativeText: '取消',
    onPositiveClick: () => runAction(`stop-${plan.id}`, async () => {
      await request.post(`/release/plans/${plan.id}/cancel`);
      message.success('已停止。');
    }),
  });
}



function editPlan(plan: ReleasePlan) {
  router.push({ path: '/release', query: { edit: String(plan.id) } });
}

function deletePlan(plan: ReleasePlan) {
  dialog.error({
    title: '删除发布计划',
    content: `确认删除 ${plan.name}？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => runAction(`delete-${plan.id}`, async () => {
      await request.delete(`/release/plans/${plan.id}`);
      message.success('已删除。');
    }),
  });
}

function openLogs(plan: ReleasePlan) {
  activeTask.value = plan.tasks?.[0] || null;
  logModalVisible.value = Boolean(activeTask.value);
}

let pollTimer: any = null;

onMounted(() => {
  loadDashboard();
  // Silently refresh the dashboard every 4 seconds
  pollTimer = setInterval(() => {
    loadDashboard('poll');
  }, 4000);
});

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer);
  }
});
</script>

<style scoped>
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  background: var(--surface-solid);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.metric-tile {
  min-height: 112px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border: 0;
  border-right: 1px solid var(--line-soft);
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  transition: background 0.16s ease;
}

.metric-tile:last-child {
  border-right: 0;
}

.metric-tile:hover {
  border-color: var(--line-soft);
  background: #fafafa;
}

.metric-tile span {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
}

.metric-tile strong {
  margin: 8px 0;
  color: var(--text);
  font-size: 30px;
  font-weight: 650;
  line-height: 1.1;
  letter-spacing: -0.03em;
  word-break: break-word;
}

.metric-tile small {
  color: var(--text-faint);
  font-size: 12px;
}

.skeleton-block {
  padding: 20px;
}

.side-grid {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 18px;
}

.health-list,
.recent-list {
  display: grid;
}

.health-row,
.recent-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
  transition: background 0.16s ease;
}

.health-row:hover,
.recent-row:hover {
  background: #fafafa;
}

.health-row:last-child,
.recent-row:last-child {
  border-bottom: 0;
}

.recent-row__meta {
  display: flex;
  align-items: flex-end;
  flex-direction: column;
  gap: 4px;
  text-align: right;
}
</style>
