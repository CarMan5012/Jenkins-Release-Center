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
        <table class="ops-table clean-table">
          <thead>
            <tr>
              <th scope="col" style="width: 16%">任务名称</th>
              <th scope="col" style="width: 15%">Job 名称</th>
              <th scope="col" style="width: 12%">状态</th>
              <th scope="col" style="width: 12%">预检校验</th>
              <th scope="col" style="width: 15%">创建时间</th>
              <th scope="col" style="width: 14%">最近运行</th>
              <th scope="col" style="width: 8%">星期</th>
              <th scope="col" style="width: 8%; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="plan in visiblePlans" :key="plan.id">
              <td>
                <RouterLink class="task-name-link" :to="`/release/${plan.id}`">
                  <strong>{{ plan.name }}</strong>
                </RouterLink>
              </td>
              <td>
                <n-tooltip v-if="plan.tasks && plan.tasks.length" trigger="hover" placement="top-start" :raw="true">
                  <template #trigger>
                    <div class="job-cell-trigger">
                      <span class="mono job-first-name">{{ plan.tasks[0].job_name }}</span>
                      <n-tag v-if="plan.tasks.length > 1" size="tiny" type="info" round :bordered="false" class="job-count-badge">
                        共{{ plan.tasks.length }}个
                      </n-tag>
                    </div>
                  </template>
                  <div class="job-tooltip-card">
                    <div class="tooltip-header">关联的 Job 清单 (共 {{ plan.tasks.length }} 个)</div>
                    <ul class="tooltip-job-list">
                      <li v-for="(task, idx) in plan.tasks" :key="task.id || idx">
                        <span class="job-seq">#{{ idx + 1 }}</span>
                        <span class="job-name mono">{{ task.job_name }}</span>
                      </li>
                    </ul>
                  </div>
                </n-tooltip>
                <span v-else class="muted mono" style="font-size: 11.5px">未绑定 Job</span>
              </td>
              <td>
                <StatusBadge :status="plan.status" />
              </td>
              <td>
                <n-tag size="small" round :bordered="false" :type="preflightTagType(plan.preflight_status)">
                  {{ getPreflightMeta(plan.preflight_status).label }}
                </n-tag>
              </td>
              <td class="mono muted" style="font-size: 12px">
                {{ formatDateTime(plan.created_at) }}
              </td>
              <td>
                <div class="cell-title">
                  <span class="mono time-text">
                    {{ plan.execute_time ? formatDateTime(plan.execute_time) : '-' }}
                  </span>
                  <span class="mono muted duration-text" v-if="plan.execute_time || Boolean(getPlanDurationSeconds(plan))">
                    耗时 {{ formatDuration(getPlanDurationSeconds(plan) || 0) }}
                  </span>
                </div>
              </td>
              <td>
                <span class="mono muted week-chip-standalone">
                  {{ getWeekDay(plan.execute_time || plan.created_at) }}
                </span>
              </td>
              <td style="text-align: right">
                <div class="action-cell">
                  <!-- 次要操作下拉菜单 (已移除“运行”按钮) -->
                  <n-dropdown
                    v-if="getActionOptions(plan).length"
                    trigger="click"
                    :options="getActionOptions(plan)"
                    @select="(key) => handleSelectAction(key, plan)"
                  >
                    <n-button size="tiny" secondary circle aria-label="操作菜单">
                      <template #icon><n-icon :component="EllipsisHorizontalOutline" /></template>
                    </n-button>
                  </n-dropdown>
                </div>
              </td>
            </tr>
            <tr v-if="!visiblePlans.length">
              <td colspan="8"><div class="empty-inline">没有匹配的发布任务。</div></td>
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
import { computed, h, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NDropdown,
  NIcon,
  NInput,
  NModal,
  NSelect,
  NSkeleton,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui';
import {
  PlayOutline,
  StopOutline,
  EllipsisHorizontalOutline,
  DocumentTextOutline,
  CreateOutline,
  TrashOutline,
} from '@vicons/ionicons5';
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
    checkAndTogglePolling();
    if (trigger === 'refresh' || trigger === 'list') {
      message.success('刷新成功');
    }
  } catch (err: any) {
    error.value = err.message || 'Dashboard 加载失败';
    message.error(err.message || '刷新失败');
  } finally {
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
    message.error(err.message || '操作失败');
  } finally {
    busyKey.value = '';
  }
}

function triggerPlan(plan: ReleasePlan) {
  if (isPreflightBlocked(plan.preflight_status)) {
    message.warning('请先完成并通过检测');
    return;
  }
  const execute = () => runAction(`run-${plan.id}`, async () => {
    await request.post(`/release/plans/${plan.id}/trigger`);
    message.success('已触发执行');
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
    content: `确认停止 ${plan.name}`,
    positiveText: '停止',
    negativeText: '取消',
    onPositiveClick: () => runAction(`stop-${plan.id}`, async () => {
      await request.post(`/release/plans/${plan.id}/cancel`);
      message.success('已停止');
    }),
  });
}



function editPlan(plan: ReleasePlan) {
  router.push({ path: '/release', query: { edit: String(plan.id) } });
}

function deletePlan(plan: ReleasePlan) {
  dialog.error({
    title: '删除发布计划',
    content: `确认删除 ${plan.name}`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => runAction(`delete-${plan.id}`, async () => {
      await request.delete(`/release/plans/${plan.id}`);
      message.success('已删除');
    }),
  });
}

function openLogs(plan: ReleasePlan) {
  activeTask.value = plan.tasks?.[0] || null;
  logModalVisible.value = Boolean(activeTask.value);
}

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) });
}

function getActionOptions(plan: ReleasePlan) {
  const options = [];
  if (plan.status === 'WAITING' && !isPreflightBlocked(plan.preflight_status)) {
    options.push({
      label: '运行任务',
      key: 'run',
      icon: renderIcon(PlayOutline),
    });
  }
  if (plan.tasks?.length) {
    options.push({
      label: '查看日志',
      key: 'log',
      icon: renderIcon(DocumentTextOutline),
    });
  }
  if (plan.status === 'RUNNING') {
    options.push({
      label: '停止任务',
      key: 'stop',
      icon: renderIcon(StopOutline),
    });
  }
  if (plan.status === 'WAITING') {
    options.push({
      label: '编辑计划',
      key: 'edit',
      icon: renderIcon(CreateOutline),
    });
  }
  if (plan.status !== 'RUNNING') {
    options.push({
      label: '删除计划',
      key: 'delete',
      icon: renderIcon(TrashOutline),
    });
  }
  return options;
}

function handleSelectAction(key: string, plan: ReleasePlan) {
  if (key === 'run') triggerPlan(plan);
  else if (key === 'log') openLogs(plan);
  else if (key === 'stop') cancelPlan(plan);
  else if (key === 'edit') editPlan(plan);
  else if (key === 'delete') deletePlan(plan);
}

import { wsService } from '../../utils/websocket';

let updateTimer: any = null;
let pollIntervalTimer: any = null;

function hasActivePlans(): boolean {
  if (!plans.value || !plans.value.length) return false;
  return plans.value.some((p: any) => ['RUNNING', 'QUEUED', 'BUILDING', 'WAITING'].includes(p.status));
}

function checkAndTogglePolling() {
  if (hasActivePlans()) {
    if (!pollIntervalTimer) {
      pollIntervalTimer = setInterval(() => {
        loadDashboard('poll');
      }, 3000);
    }
  } else {
    stopPolling();
  }
}

function stopPolling() {
  if (pollIntervalTimer) {
    clearInterval(pollIntervalTimer);
    pollIntervalTimer = null;
  }
}

function handleDashboardUpdate() {
  if (updateTimer) clearTimeout(updateTimer);
  updateTimer = setTimeout(() => {
    loadDashboard('poll');
  }, 200);
}

onMounted(async () => {
  await loadDashboard();
  checkAndTogglePolling();
  wsService.on('RELEASE_UPDATE', handleDashboardUpdate);
});

onUnmounted(() => {
  stopPolling();
  if (updateTimer) clearTimeout(updateTimer);
  wsService.off('RELEASE_UPDATE', handleDashboardUpdate);
});
</script>

<style scoped>
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  background: transparent;
  border: none;
  box-shadow: none;
}

.metric-tile {
  min-height: 116px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: var(--radius-lg, 16px);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
  transition: all 0.28s cubic-bezier(0.25, 0.8, 0.25, 1);
}

.metric-tile:hover {
  transform: translateY(-3px);
  border-color: rgba(0, 113, 227, 0.3);
  box-shadow: 0 10px 30px rgba(0, 113, 227, 0.1);
  background: rgba(255, 255, 255, 0.88);
}

.metric-tile span {
  color: var(--text-muted);
  font-size: 12.5px;
  font-weight: 550;
  letter-spacing: -0.01em;
}

.metric-tile strong {
  margin: 6px 0;
  background: linear-gradient(135deg, #1d1d1f 0%, #0071e3 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  font-size: 32px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.035em;
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

.clean-table th,
.clean-table td {
  padding: 13px 16px;
}

.task-name-link {
  font-size: 13.5px;
  color: var(--text);
}

.sub-job-name {
  font-size: 11.5px;
}

.status-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.time-text {
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.week-chip {
  color: var(--text-muted);
  font-size: 11px;
}

.duration-text {
  font-size: 11.5px;
}

.creator-badge {
  display: inline-block;
  padding: 2px 8px;
  background: var(--surface-subtle);
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--text-muted);
}

.action-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.job-cell-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 160px;
  cursor: pointer;
}

.job-first-name {
  font-size: 11.5px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.job-count-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 0 4px;
  height: 15px;
  line-height: 15px;
  flex-shrink: 0;
}

.job-tooltip-card {
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.12), 0 2px 6px rgba(0, 0, 0, 0.04);
  padding: 12px 14px;
  min-width: 190px;
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
}

.tooltip-header {
  font-size: 11px;
  font-weight: 650;
  color: var(--text-faint);
  margin-bottom: 8px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 6px;
  letter-spacing: -0.01em;
}

.tooltip-job-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tooltip-job-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text);
}

.job-seq {
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  background: var(--primary-soft);
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.week-chip-standalone {
  display: inline-block;
  padding: 2px 6px;
  background: var(--surface-subtle);
  border-radius: 5px;
  font-size: 11.5px;
  color: var(--text-muted);
}
</style>
