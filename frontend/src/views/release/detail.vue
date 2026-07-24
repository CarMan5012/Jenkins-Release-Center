<template>
  <section class="page detail-page">
    <div class="page-header">
      <div>
        <h1>{{ plan?.name || '发布详情' }}</h1>
        <p class="mono">Plan #{{ route.params.id }}</p>
      </div>
      <div class="header-actions">
        <n-button secondary @click="router.push('/release')">返回列表</n-button>
        <n-button secondary :disabled="!plan" :loading="busyKey === 'preflight'" @click="preflightPlan">发布前检查</n-button>
        <n-button type="primary" :disabled="plan?.status !== 'WAITING' || isPreflightBlocked(plan?.preflight_status)" :title="isPreflightBlocked(plan?.preflight_status) ? '请先完成并通过发布前检查' : undefined" :loading="busyKey === 'run'" @click="triggerPlan">运行</n-button>
        <n-button type="warning" secondary :disabled="!plan || !['WAITING', 'RUNNING'].includes(plan.status)" :loading="busyKey === 'stop'" @click="cancelPlan">停止</n-button>
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

    <div v-if="loading" class="panel skeleton-block">
      <n-skeleton text :repeat="10" />
    </div>

    <template v-else-if="plan">
      <div class="detail-summary panel">
        <div>
          <span class="summary-label">状态</span>
          <StatusBadge :status="plan.status" />
        </div>
        <div>
          <span class="summary-label">类型</span>
          <strong>{{ formatPlanType(plan.type) }}</strong>
        </div>
        <div>
          <span class="summary-label">任务数</span>
          <strong>{{ plan.tasks.length }}</strong>
        </div>
        <div>
          <span class="summary-label">总耗时</span>
          <strong class="mono">{{ formatDuration(getPlanDurationSeconds(plan)) }}</strong>
        </div>
        <div>
          <span class="summary-label">计划时间</span>
          <strong class="mono">{{ formatDateTime(plan.execute_time || plan.created_at) }}</strong>
        </div>
        <div>
          <span class="summary-label">预检状态</span>
          <n-tag size="small" :type="preflightTagType(plan.preflight_status)">{{ getPreflightMeta(plan.preflight_status).label }}</n-tag>
          <span class="muted mono">{{ formatDateTime(plan.preflight_checked_at) }}</span>
        </div>
      </div>

      <PreflightResult :result="plan.preflight_result" :checked-at="plan.preflight_checked_at" />

      <n-tabs v-model:value="activeTab" type="segment" animated>
        <n-tab-pane name="overview" tab="概览">
          <section class="panel">
            <div class="panel__header">
              <h2 class="panel__title">任务拓扑</h2>
            </div>
            <div class="table-wrap">
              <table class="ops-table task-table">
                <thead>
                  <tr>
                    <th scope="col">序号</th>
                    <th scope="col">Job</th>
                    <th scope="col">分支</th>
                    <th scope="col">状态</th>
                    <th scope="col">构建号</th>
                    <th scope="col">耗时</th>
                    <th scope="col">日志</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="task in orderedTasks" :key="task.id" :class="{ 'selected-row': selectedTaskId === task.id }">
                    <td class="mono">{{ task.sequence }}</td>
                    <td>
                      <div class="cell-title">
                        <strong>{{ task.job_name }}</strong>
                        <span class="muted mono">server #{{ task.server_id }} · job #{{ task.job_id }}</span>
                      </div>
                    </td>
                    <td class="mono">{{ task.branch }}</td>
                    <td><StatusBadge :status="task.status" /></td>
                    <td class="mono">{{ task.build_number || '-' }}</td>
                    <td class="mono">{{ formatDuration(task.duration) }}</td>
                    <td>
                      <div style="display: flex; gap: 6px; align-items: center;">
                        <n-button size="tiny" secondary @click="showLog(task.id, null)">查看日志</n-button>
                        <RefreshButton
                          v-if="['RUNNING', 'FAILED'].includes(task.status) && task.build_number" 
                          size="tiny" 
                          type="info"
                          secondary 
                          label="同步"
                          :loading="syncingTaskId === task.id"
                          @click="syncTask(task.id)"
                        />
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </n-tab-pane>

        <n-tab-pane name="history" tab="执行历史">
          <section class="panel">
            <div class="panel__header">
              <h2 class="panel__title">关联历史</h2>
              <RefreshButton size="small" secondary label="刷新" :loading="historyLoading" @click="loadHistory('refresh')" />



            </div>
            <div class="table-wrap">
              <table class="ops-table">
                <thead>
                  <tr>
                    <th scope="col">ID</th>
                    <th scope="col">Job</th>
                    <th scope="col">分支</th>
                    <th scope="col">状态</th>
                    <th scope="col">触发人</th>
                    <th scope="col">耗时</th>
                    <th scope="col">时间</th>
                    <th scope="col">日志</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in relatedHistory" :key="item.id">
                    <td class="mono">#{{ item.id }}</td>
                    <td>{{ item.job_name || '-' }}</td>
                    <td class="mono">{{ item.branch || '-' }}</td>
                    <td><StatusBadge :status="item.status" /></td>
                    <td>{{ formatTriggerBy(item.trigger_by) }}</td>
                    <td class="mono">{{ formatDuration(item.duration) }}</td>
                    <td class="mono">{{ formatDateTime(item.started_at || item.created_at) }}</td>
                    <td><n-button size="tiny" secondary @click="showLog(item.task_id, item.id)">查看日志</n-button></td>
                  </tr>
                  <tr v-if="!relatedHistory.length">
                    <td colspan="8"><div class="empty-inline">暂无关联历史。</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </n-tab-pane>

        <n-tab-pane name="logs" tab="日志">
          <section class="panel log-panel">
            <div class="task-picker">
              <n-select v-model:value="selectedTaskId" :options="taskOptions" style="width: 320px" />
            </div>
            <LogViewer :task-id="selectedTaskId" :history-id="selectedHistoryId" :title="selectedLogTitle" :build-number="selectedLogBuildNumber" />
          </section>
        </n-tab-pane>

        <n-tab-pane name="config" tab="配置">
          <section class="panel key-grid">
            <div><span>计划名称</span><strong>{{ plan.name }}</strong></div>
            <div><span>执行类型</span><strong>{{ formatPlanType(plan.type) }}</strong></div>
            <div><span>失败策略</span><strong>{{ plan.pipeline_failure_strategy }}</strong></div>
            <div><span>批次间隔</span><strong class="mono">{{ plan.interval_minutes }} min</strong></div>
            <div><span>创建时间</span><strong class="mono">{{ formatDateTime(plan.created_at) }}</strong></div>
            <div><span>更新时间</span><strong class="mono">{{ formatDateTime(plan.updated_at) }}</strong></div>
          </section>
        </n-tab-pane>

        <n-tab-pane name="params" tab="参数">
          <section class="panel">
            <div class="table-wrap">
              <table class="ops-table">
                <thead>
                  <tr>
                    <th scope="col">任务</th>
                    <th scope="col">分支</th>
                    <th scope="col">参数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="task in orderedTasks" :key="task.id">
                    <td>{{ task.job_name }}</td>
                    <td class="mono">{{ task.branch }}</td>
                    <td>
                      <code v-if="Object.keys(task.parameters || {}).length" class="mono param-code">{{ JSON.stringify(task.parameters) }}</code>
                      <span v-else class="muted">-</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </n-tab-pane>

        <n-tab-pane name="records" tab="操作记录">
          <section class="panel record-panel">
            <n-timeline>
              <n-timeline-item type="info" title="计划创建" :time="formatDateTime(plan.created_at)" />
              <n-timeline-item v-for="task in orderedTasks" :key="task.id" :type="timelineType(task.status)" :title="`${task.job_name} · ${task.status}`" :time="formatDateTime(task.updated_at)" />
              <n-timeline-item type="default" title="最近更新" :time="formatDateTime(plan.updated_at)" />
            </n-timeline>
          </section>
        </n-tab-pane>
      </n-tabs>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NSelect, NSkeleton, NTabPane, NTabs, NTag, NTimeline, NTimelineItem, useDialog, useMessage } from 'naive-ui';
import LogViewer from '../../components/LogViewer.vue';
import PreflightResult from '../../components/PreflightResult.vue';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import { formatDateTime, formatDuration, formatPlanType, getPlanDurationSeconds, formatTriggerBy, getPreflightMeta, isPreflightBlocked } from '../../utils/release-ui';
import type { PreflightStatus } from '../../utils/release-ui';

interface ReleaseTask {
  id: number;
  server_id: number;
  job_id: number;
  job_name: string;
  branch: string;
  parameters: Record<string, unknown>;
  sequence: number;
  depends_on_task_id?: number | null;
  status: string;
  build_number?: number | null;
  duration: number;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at: string;
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
  updated_at: string;
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

interface ReleaseHistory {
  id: number;
  task_id: number;
  plan_id: number;
  job_name?: string;
  branch?: string;
  build_number?: number | null;
  status?: string;
  trigger_by?: string;
  duration: number;
  started_at?: string;
  created_at: string;
}

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const loading = ref(true);
const historyLoading = ref(false);
const syncingTaskId = ref<number | null>(null);
const busyKey = ref('');
const error = ref('');
const plan = ref<ReleasePlan | null>(null);
const histories = ref<ReleaseHistory[]>([]);
const selectedTaskId = ref<number | null>(null);
const selectedHistoryId = ref<number | null>(null);
const activeTab = ref('overview');

function showLog(taskId: number | null, historyId: number | null = null) {
  selectedTaskId.value = taskId;
  selectedHistoryId.value = historyId;
  activeTab.value = 'logs';
}

import { watch } from 'vue';
watch(selectedTaskId, (val) => {
  if (val) {
    selectedHistoryId.value = null;
  }
});

const orderedTasks = computed(() => [...(plan.value?.tasks || [])].sort((a, b) => a.sequence - b.sequence));
const selectedTask = computed(() => orderedTasks.value.find((task) => task.id === selectedTaskId.value) || orderedTasks.value[0]);
const taskOptions = computed(() => orderedTasks.value.map((task) => ({ label: `${task.sequence} · ${task.job_name}`, value: task.id })));
const relatedHistory = computed(() => histories.value.filter((item) => item.plan_id === plan.value?.id));

const selectedLogTitle = computed(() => {
  if (selectedHistoryId.value) {
    const hist = histories.value.find(h => h.id === selectedHistoryId.value);
    return hist ? `${hist.job_name} (外部构建)` : 'Console 日志';
  }
  return selectedTask.value?.job_name || 'Console 日志';
});
const selectedLogBuildNumber = computed(() => {
  if (selectedHistoryId.value) {
    const hist = histories.value.find(h => h.id === selectedHistoryId.value);
    return hist ? hist.build_number : null;
  }
  return selectedTask.value?.build_number || null;
});

function preflightTagType(status: PreflightStatus): 'default' | 'success' | 'warning' | 'error' {
  const tone = getPreflightMeta(status).tone;
  if (tone === 'success' || tone === 'warning') return tone;
  return tone === 'danger' ? 'error' : 'default';
}

async function loadPlan() {
  loading.value = true;
  error.value = '';
  try {
    const res = await request.get(`/release/plans/${route.params.id}`);
    plan.value = res.data;
    selectedTaskId.value = orderedTasks.value[0]?.id || null;
  } catch (err: any) {
    error.value = err.message || '发布详情加载失败。';
  } finally {
    loading.value = false;
  }
}

async function loadHistory(trigger?: 'page' | 'refresh') {
  const isManual = trigger === 'refresh';
  if (isManual) {
    historyLoading.value = true;
  }
  const startTime = Date.now();
  try {
    const res = await request.get('/history', { params: { page: 1, limit: 50 } });
    histories.value = res.data || [];
  } catch (err: any) {
    message.error(err.message || '历史记录加载失败。');
  } finally {
    if (isManual) {
      const elapsed = Date.now() - startTime;
      if (elapsed < 500) {
        await new Promise((resolve) => setTimeout(resolve, 500 - elapsed));
      }
      historyLoading.value = false;
    }
  }
}

async function syncTask(taskId: number) {
  syncingTaskId.value = taskId;
  try {
    const res = await request.post(`/release/tasks/${taskId}/sync`);
    message.success(res.data?.message || '状态同步成功。');
    await loadPlan();
    await loadHistory();
  } catch (err: any) {
    message.error(err.message || '状态同步失败。');
  } finally {
    syncingTaskId.value = null;
  }
}

async function runAction(key: string, action: () => Promise<void>) {
  busyKey.value = key;
  try {
    await action();
    await loadPlan();
    await loadHistory();
  } catch (err: any) {
    message.error(err.message || '操作失败。');
  } finally {
    busyKey.value = '';
  }
}

function triggerPlan() {
  if (!plan.value) return;
  if (isPreflightBlocked(plan.value.preflight_status)) {
    message.warning('请先完成并通过发布前检查。');
    return;
  }
  const execute = () => runAction('run', async () => {
    await request.post(`/release/plans/${plan.value?.id}/trigger`);
    message.success('已触发执行。');
  });
  if (plan.value.preflight_status === 'WARNING') {
    dialog.warning({
      title: '预检存在警告',
      content: '最近一次检查存在临时性问题，仍要运行吗？',
      positiveText: '仍然运行',
      negativeText: '取消',
      onPositiveClick: execute,
    });
    return;
  }
  execute();
}

async function preflightPlan() {
  if (!plan.value) return;
  busyKey.value = 'preflight';
  try {
    const response = await request.post(`/release/plans/${plan.value?.id}/preflight`);
    plan.value = response.data;
    message.success('发布前检查已完成。');
  } catch (err: any) {
    message.error(err.message || '发布前检查失败。');
  } finally {
    busyKey.value = '';
  }
}

function cancelPlan() {
  if (!plan.value) return;
  dialog.warning({
    title: '停止发布任务',
    content: `确认停止 ${plan.value.name}？`,
    positiveText: '停止',
    negativeText: '取消',
    onPositiveClick: () => runAction('stop', async () => {
      await request.post(`/release/plans/${plan.value?.id}/cancel`);
      message.success('已停止。');
    }),
  });
}

function timelineType(status: string): 'success' | 'error' | 'warning' | 'info' | 'default' {
  if (status === 'SUCCESS') return 'success';
  if (status === 'FAILED') return 'error';
  if (status === 'RUNNING') return 'warning';
  if (status === 'WAITING') return 'info';
  return 'default';
}

onMounted(async () => {
  await loadPlan();
  await loadHistory();
});
</script>

<style scoped>
.header-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.skeleton-block {
  padding: 20px;
}

.detail-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

.detail-summary > div {
  min-height: 88px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border-right: 1px solid var(--line-soft);
}

.detail-summary > div:last-child {
  border-right: 0;
}

.summary-label,
.key-grid span {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 550;
}

.selected-row td {
  background: var(--primary-soft) !important;
}

.log-panel,
.record-panel {
  padding: 18px;
}

.task-picker {
  margin-bottom: 14px;
}

.key-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.key-grid > div {
  min-height: 82px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
}

.param-code {
  display: inline-block;
  max-width: 620px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
