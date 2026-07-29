<template>
  <section class="page release-page">
    <div class="page-header">
      <div>
        <h1>发布计划</h1>
        <p>创建、排程和管理 Jenkins 发布任务。</p>
      </div>
      <n-button type="primary" @click="openCreateWizard">创建发布计划</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

    <div class="toolbar">
      <n-input v-model:value="keyword" clearable placeholder="搜索计划名称" style="width: 260px" />
      <n-select v-model:value="statusFilter" :options="statusOptions" style="width: 150px" />
      <n-select v-model:value="typeFilter" :options="typeOptions" style="width: 160px" />
      <n-select v-model:value="sortKey" :options="sortOptions" style="width: 170px" />
      <RefreshButton secondary label="刷新" :loading="btnRefreshLoading" @click="loadPlans('refresh')" />
    </div>

    <section class="panel">
      <div class="panel__header">
        <h2 class="panel__title">计划列表</h2>
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
              <th scope="col">预检状态</th>
              <th scope="col">类型</th>
              <th scope="col">创建时间</th>
              <th scope="col">调度时间</th>
              <th scope="col">星期</th>
              <th scope="col">任务数</th>
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
              <td><StatusBadge :status="plan.status" /></td>
              <td>
                <n-tag size="small" :type="preflightTagType(plan.preflight_status)">{{ getPreflightMeta(plan.preflight_status).label }}</n-tag>
              </td>
              <td>{{ formatPlanType(plan.type) }}</td>
              <td class="mono">{{ formatDateTime(plan.created_at) }}</td>
              <td class="mono">{{ formatDateTime(plan.execute_time) }}</td>
              <td class="mono">{{ getWeekDay(plan.execute_time) }}</td>
              <td class="mono">{{ plan.tasks?.length || 0 }}</td>
              <td>
                <div class="cell-actions">
                  <n-button size="tiny" type="primary" :loading="busyKey === `run-${plan.id}`" :disabled="plan.status === 'RUNNING' || busyKey === `preflight-${plan.id}` || isPreflightBlocked(plan.preflight_status)" :title="isPreflightBlocked(plan.preflight_status) ? '请先完成并通过检测' : undefined" @click="triggerPlan(plan)">运行</n-button>
                  <n-button size="tiny" secondary :loading="busyKey === `preflight-${plan.id}`" @click="preflightPlan(plan)">检测</n-button>
                  <n-button size="tiny" type="warning" secondary :loading="busyKey === `cancel-${plan.id}`" :disabled="!['WAITING', 'RUNNING'].includes(plan.status)" @click="cancelPlan(plan)">停止</n-button>
                  <n-button size="tiny" secondary :disabled="plan.status !== 'WAITING'" @click="openEditWizard(plan)">编辑</n-button>
                  <n-button size="tiny" secondary @click="$router.push(`/release/${plan.id}`)">日志</n-button>
                  <n-button size="tiny" type="error" secondary :loading="busyKey === `delete-${plan.id}`" :disabled="plan.status === 'RUNNING'" @click="deletePlan(plan)">删除</n-button>
                </div>
              </td>
            </tr>
            <tr v-if="!visiblePlans.length">
              <td colspan="8"><div class="empty-inline">没有发布计划。</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <n-modal v-model:show="showWizard" preset="card" :title="wizardMode === 'edit' ? '编辑发布计划' : '创建发布计划'" style="width: min(920px, 96vw)">
      <n-steps :current="currentStep" size="small" class="wizard-steps">
        <n-step title="基础" />
        <n-step title="任务" />
        <n-step title="确认" />
      </n-steps>

      <div v-if="currentStep === 1" class="wizard-section">
        <n-form label-placement="left" label-align="right" label-width="100">
          <n-form-item label="计划名称" required>
            <n-input v-model:value="wizardForm.name" placeholder="例如 prod-api-2026.07.09" />
          </n-form-item>
          <n-form-item label="执行类型" required>
            <n-select v-model:value="wizardForm.type" :options="typeOptions.filter((item) => item.value !== 'ALL')" />
          </n-form-item>
          <n-form-item v-if="wizardForm.type !== 'IMMEDIATE'" label="调度时间" required>
            <div class="schedule-time-control">
              <n-date-picker
                v-model:value="wizardForm.execute_time"
                type="datetime"
                clearable
                style="width: 100%"
                panel-class="release-time-picker-panel"
              >
                <template #footer>
                  <div class="quick-time-in-footer">
                    <n-button size="tiny" secondary @click="setQuickExecuteTime(21, 30)">21:30</n-button>
                    <n-button size="tiny" secondary @click="setQuickExecuteTime(22, 0)">22:00</n-button>
                  </div>
                </template>
              </n-date-picker>
            </div>
          </n-form-item>
          <n-form-item v-if="wizardForm.type === 'BATCH'" label="批次间隔">
            <n-input-number v-model:value="wizardForm.interval_minutes" :min="1" style="width: 180px" />
          </n-form-item>
          <n-form-item v-if="wizardForm.type === 'PIPELINE'" label="失败策略">
            <n-select v-model:value="wizardForm.pipeline_strategy" :options="strategyOptions" />
          </n-form-item>
          <n-form-item label="钉钉通知">
            <div style="display: flex; align-items: center; min-height: 34px; gap: 10px;">
              <n-switch v-model:value="wizardForm.notify_dingtalk" :disabled="dingtalkStatus !== 'ENABLED'" />
              <span v-if="dingtalkStatus === 'NONE'" style="color: #fa8c16; font-size: 13px;">
                系统未配置钉钉机器人，请先至系统设置中配置
              </span>
              <span v-else-if="dingtalkStatus === 'DISABLED'" style="color: #fa8c16; font-size: 13px;">
                系统钉钉机器人已被禁用，请先至系统设置中启用
              </span>
              <span v-else style="color: #8c8c8c; font-size: 13px;">
                {{ wizardForm.notify_dingtalk ? '发布状态变更时将自动推送钉钉消息' : '默认不推送钉钉通知' }}
              </span>
            </div>
          </n-form-item>
        </n-form>
      </div>

      <div v-if="currentStep === 2" class="wizard-section task-editor">
        <n-alert type="info" show-icon style="margin-bottom: 14px">
          💡 操作提示：请依次选择 Jenkins 实例、View 视图和 Job 任务。分支下拉框将自动加载候选分支，您也可以直接在框中手动输入自定义分支或 Tag 标签。
        </n-alert>
        <div v-for="(task, index) in wizardForm.tasks" :key="index" class="task-row">
          <div class="task-row__header">
            <strong>任务 #{{ index + 1 }}</strong>
            <n-button v-if="wizardForm.tasks.length > 1" size="tiny" type="error" secondary @click="removeTaskRow(index)">删除</n-button>
          </div>
          <div class="task-row__grid">
            <n-select v-model:value="task.server_id" :options="serverOptions" placeholder="请选择 Jenkins 实例" @update:value="(value) => onTaskServerChange(Number(value), index)" />
            <n-select v-model:value="task.view_id" :options="taskViewOptions[index] || []" placeholder="请选择 View 视图" @update:value="(value) => onTaskViewChange(Number(value), index)" />
            <n-select v-model:value="task.job_id" :options="taskJobOptions[index] || []" placeholder="请选择 Job 任务" @update:value="(value) => onTaskJobChange(Number(value), index)" />
            <n-select v-model:value="task.branch" :options="taskBranchOptions[index] || []" :placeholder="!task.job_id ? '请先选择 Job 任务' : '选择分支或手动输入分支/Tag'" filterable tag title="提示：可从下拉列表中选择分支，也可直接手动输入分支/Tag" />
          </div>
        </div>
        <n-button dashed block type="primary" @click="addTaskRow">添加任务</n-button>
      </div>

      <div v-if="currentStep === 3" class="wizard-section summary-box">
        <div class="key-grid">
          <div><span>名称</span><strong>{{ wizardForm.name }}</strong></div>
          <div><span>类型</span><strong>{{ formatPlanType(wizardForm.type) }}</strong></div>
          <div><span>任务数</span><strong class="mono">{{ wizardForm.tasks.length }}</strong></div>
          <div><span>时间</span><strong class="mono">{{ wizardForm.execute_time ? new Date(wizardForm.execute_time).toLocaleString() : '立即执行' }}</strong></div>
          <div><span>钉钉通知</span><strong :style="{ color: wizardForm.notify_dingtalk ? '#1890ff' : 'inherit' }">{{ wizardForm.notify_dingtalk ? '开启' : '关闭' }}</strong></div>
        </div>
        <div class="table-wrap">
          <table class="ops-table preview-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Server</th>
                <th scope="col">View</th>
                <th scope="col">Job</th>
                <th scope="col">Branch</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(task, index) in wizardForm.tasks" :key="index">
                <td class="mono">{{ index }}</td>
                <td>{{ optionLabel(serverOptions, task.server_id) }}</td>
                <td>{{ optionLabel(taskViewOptions[index] || [], task.view_id) }}</td>
                <td>{{ optionLabel(taskJobOptions[index] || [], task.job_id) }}</td>
                <td class="mono">{{ task.branch }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <template #action>
        <div class="wizard-actions">
          <n-button secondary :disabled="currentStep === 1" @click="currentStep--">上一步</n-button>
          <div>
            <n-button secondary @click="showWizard = false">取消</n-button>
            <n-button v-if="currentStep < 3" type="primary" @click="nextStep">下一步</n-button>
            <n-button v-else type="primary" :loading="submitLoading" @click="submitPlan">提交</n-button>
          </div>
        </div>
      </template>
    </n-modal>

    <n-modal v-model:show="showPreflight" preset="card" :title="selectedPreflight?.name ? `检测结果 - ${selectedPreflight.name}` : '检测结果'" style="width: min(760px, 94vw)">
      <PreflightResult :result="selectedPreflight?.preflight_result" :checked-at="selectedPreflight?.preflight_checked_at" />
    </n-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSkeleton,
  NStep,
  NSteps,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui';
import PreflightResult from '../../components/PreflightResult.vue';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import { filterPlans, formatDateTime, formatPlanType, getPreflightMeta, getQuickExecuteTime, isPreflightBlocked, sortPlans, getWeekDay } from '../../utils/release-ui';
import type { PreflightStatus } from '../../utils/release-ui';

type SelectOption = { label: string; value: number | string };

interface ReleaseTask {
  id?: number;
  server_id: number | null;
  view_id?: number | null;
  job_id: number | null;
  job_name?: string;
  branch: string | null;
  parameters?: Record<string, unknown>;
  sequence?: number;
  depends_on_task_id?: number | null;
  depends_on_sequence?: number | null;
  error_message?: string | null;
}

interface ReleasePlan {
  id: number;
  name: string;
  type: string;
  execute_time?: string | null;
  interval_minutes: number;
  pipeline_failure_strategy: string;
  notify_dingtalk?: boolean;
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

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const pageLoading = ref(false);
const btnRefreshLoading = ref(false);
const error = ref('');
const busyKey = ref('');
const plans = ref<ReleasePlan[]>([]);
const servers = ref<Array<{ id: number; name: string; is_active: number }>>([]);
const dingtalkStatus = ref<'NONE' | 'DISABLED' | 'ENABLED'>('NONE');
const hasDingTalkConfig = computed(() => dingtalkStatus.value === 'ENABLED');
const keyword = ref('');
const statusFilter = ref('ALL');
const typeFilter = ref('ALL');
const sortKey = ref('created_desc');
const showPreflight = ref(false);
const selectedPreflight = ref<ReleasePlan | null>(null);

const showWizard = ref(false);
const wizardMode = ref<'create' | 'edit'>('create');
const editingPlanId = ref<number | null>(null);
const currentStep = ref(1);
const submitLoading = ref(false);
const taskViewOptions = ref<Record<number, SelectOption[]>>({});
const taskJobOptions = ref<Record<number, SelectOption[]>>({});
const taskBranchOptions = ref<Record<number, SelectOption[]>>({});

const wizardForm = ref({
  name: '',
  type: 'SCHEDULED',
  execute_time: null as number | null,
  interval_minutes: 2,
  pipeline_strategy: 'STOP',
  notify_dingtalk: false,
  tasks: [emptyTask()],
});

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

const strategyOptions = [
  { label: '失败停止', value: 'STOP' },
  { label: '失败继续', value: 'CONTINUE' },
];

const serverOptions = computed<SelectOption[]>(() => servers.value.map((server) => ({ label: server.name, value: server.id })));
const visiblePlans = computed(() => sortPlans(filterPlans(plans.value, keyword.value, statusFilter.value, typeFilter.value), sortKey.value));

function preflightTagType(status: PreflightStatus): 'default' | 'success' | 'warning' | 'error' {
  const tone = getPreflightMeta(status).tone;
  if (tone === 'success' || tone === 'warning') return tone;
  return tone === 'danger' ? 'error' : 'default';
}

function emptyTask(): ReleaseTask {
  return {
    server_id: null,
    view_id: null,
    job_id: null,
    branch: null,
    depends_on_task_id: null,
  };
}

function setQuickExecuteTime(hour: number, minute: number) {
  wizardForm.value.execute_time = getQuickExecuteTime(wizardForm.value.execute_time, hour, minute);
}

async function loadPlans(trigger?: 'page' | 'refresh') {
  if (trigger === 'refresh') {
    btnRefreshLoading.value = true;
  } else {
    pageLoading.value = true;
  }
  const startTime = Date.now();
  error.value = '';
  try {
    const res = await request.get('/release/plans');
    plans.value = res.data || [];
  } catch (err: any) {
    error.value = err.message || '发布计划加载失败。';
  } finally {
    const elapsed = Date.now() - startTime;
    if (elapsed < 500) {
      await new Promise((resolve) => setTimeout(resolve, 500 - elapsed));
    }
    pageLoading.value = false;
    btnRefreshLoading.value = false;
  }
}

async function loadServers() {
  try {
    const res = await request.get('/jenkins/servers');
    servers.value = res.data || [];
  } catch (err: any) {
    message.error(err.message || 'Jenkins 实例加载失败。');
  }
}

async function runAction(key: string, action: () => Promise<void>) {
  busyKey.value = key;
  try {
    await action();
    await loadPlans();
  } catch (err: any) {
    message.error(err.message || '操作失败。');
  } finally {
    busyKey.value = '';
  }
}

function triggerPlan(plan: ReleasePlan) {
  if (busyKey.value === `preflight-${plan.id}`) return;
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

async function preflightPlan(plan: ReleasePlan) {
  busyKey.value = `preflight-${plan.id}`;
  try {
    const response = await request.post(`/release/plans/${plan.id}/preflight`);
    selectedPreflight.value = response.data;
    showPreflight.value = true;
    await loadPlans();
  } catch (err: any) {
    message.error(err.message || '检测失败。');
  } finally {
    busyKey.value = '';
  }
}

function cancelPlan(plan: ReleasePlan) {
  dialog.warning({
    title: '停止发布计划',
    content: `确认停止 ${plan.name}？`,
    positiveText: '停止',
    negativeText: '取消',
    onPositiveClick: () => runAction(`cancel-${plan.id}`, async () => {
      await request.post(`/release/plans/${plan.id}/cancel`);
      message.success('已停止。');
    }),
  });
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



async function checkDingTalkConfig() {
  try {
    const res = await request.get('/system/notify-configs');
    const configs = res.data || [];
    const dtConfigs = configs.filter((c: any) => c.channel_type === 'DINGTALK' && c.webhook_url);
    if (!dtConfigs.length) {
      dingtalkStatus.value = 'NONE';
    } else if (!dtConfigs.some((c: any) => c.is_active)) {
      dingtalkStatus.value = 'DISABLED';
    } else {
      dingtalkStatus.value = 'ENABLED';
    }
  } catch {
    dingtalkStatus.value = 'NONE';
  }
}

function buildPayload(plan: ReleasePlan, name = plan.name, type = plan.type) {
  return {
    name,
    type,
    execute_time: type === 'IMMEDIATE' ? null : plan.execute_time,
    interval_minutes: plan.interval_minutes || 0,
    pipeline_failure_strategy: plan.pipeline_failure_strategy || 'STOP',
    notify_dingtalk: Boolean(plan.notify_dingtalk),
    tasks: plan.tasks.map((task, index) => ({
      server_id: task.server_id,
      job_id: task.job_id,
      job_name: task.job_name,
      branch: task.branch || '',
      parameters: task.parameters || {},
      sequence: index,
      depends_on_sequence: type === 'PIPELINE' && index > 0 ? index - 1 : null,
    })),
  };
}

async function openCreateWizard() {
  await checkDingTalkConfig();
  wizardMode.value = 'create';
  editingPlanId.value = null;
  currentStep.value = 1;
  taskViewOptions.value = {};
  taskJobOptions.value = {};
  taskBranchOptions.value = {};
  wizardForm.value = {
    name: '',
    type: 'SCHEDULED',
    execute_time: null,
    interval_minutes: 2,
    pipeline_strategy: 'STOP',
    notify_dingtalk: false,
    tasks: [emptyTask()],
  };
  if (servers.value.length === 1) {
    wizardForm.value.tasks[0].server_id = servers.value[0].id;
    onTaskServerChange(servers.value[0].id, 0);
  }
  showWizard.value = true;
}

async function openEditWizard(plan: ReleasePlan) {
  await checkDingTalkConfig();
  wizardMode.value = 'edit';
  editingPlanId.value = plan.id;
  currentStep.value = 1;
  taskViewOptions.value = {};
  taskJobOptions.value = {};
  taskBranchOptions.value = {};

  const tasks: ReleaseTask[] = [];
  for (let index = 0; index < plan.tasks.length; index++) {
    const task = plan.tasks[index];
    tasks.push({
      server_id: task.server_id,
      view_id: task.view_id || null,
      job_id: task.job_id,
      job_name: task.job_name,
      branch: task.branch,
      depends_on_task_id: task.depends_on_task_id || null,
      parameters: task.parameters || {},
    });
    if (task.server_id) await loadTaskViews(task.server_id, index);
    if (task.server_id && task.view_id) await loadTaskJobs(task.server_id, task.view_id, index);
    if (task.server_id && task.job_id) await loadTaskBranches(task.server_id, task.job_id, index);
  }

  wizardForm.value = {
    name: plan.name,
    type: plan.type,
    execute_time: plan.execute_time ? new Date(plan.execute_time).getTime() : null,
    interval_minutes: plan.interval_minutes || 2,
    pipeline_strategy: plan.pipeline_failure_strategy || 'STOP',
    notify_dingtalk: hasDingTalkConfig.value ? Boolean(plan.notify_dingtalk) : false,
    tasks: tasks.length ? tasks : [emptyTask()],
  };
  showWizard.value = true;
}

function nextStep() {
  if (currentStep.value === 1) {
    if (!wizardForm.value.name.trim()) {
      message.error('请填写计划名称。');
      return;
    }
    if (wizardForm.value.type !== 'IMMEDIATE' && !wizardForm.value.execute_time) {
      message.error('请设置调度时间。');
      return;
    }
  }
  if (currentStep.value === 2) {
    for (let i = 0; i < wizardForm.value.tasks.length; i++) {
      const task = wizardForm.value.tasks[i];
      if (!task.server_id) {
        message.error(`任务 #${i + 1} 请选择 Jenkins 实例。`);
        return;
      }
      if (!task.view_id) {
        message.error(`任务 #${i + 1} 请选择 View 视图。`);
        return;
      }
      if (!task.job_id) {
        message.error(`任务 #${i + 1} 请选择 Job 任务。`);
        return;
      }
      if (!task.branch) {
        message.error(`任务 #${i + 1} 请选择或手动输入分支/Tag。`);
        return;
      }
    }
  }
  currentStep.value += 1;
}

function addTaskRow() {
  const next = emptyTask();
  if (servers.value.length === 1) next.server_id = servers.value[0].id;
  wizardForm.value.tasks.push(next);
  const index = wizardForm.value.tasks.length - 1;
  if (next.server_id) loadTaskViews(next.server_id, index);
}

function removeTaskRow(index: number) {
  wizardForm.value.tasks.splice(index, 1);
}

async function loadTaskViews(serverId: number, index: number) {
  const res = await request.get(`/jenkins/servers/${serverId}/views`);
  taskViewOptions.value[index] = (res.data || []).map((view: any) => ({ label: view.name, value: view.id }));
}

async function loadTaskJobs(serverId: number, viewId: number, index: number) {
  const res = await request.get(`/jenkins/servers/${serverId}/views/${viewId}/jobs`);
  taskJobOptions.value[index] = (res.data || []).map((job: any) => ({ label: job.name, value: job.id }));
}

async function loadTaskBranches(serverId: number, jobId: number, index: number) {
  try {
    const res = await request.get(`/jenkins/servers/${serverId}/jobs/${jobId}/branches`);
    taskBranchOptions.value[index] = (res.data || []).map((branch: string) => ({ label: branch, value: branch }));
  } catch (err: any) {
    taskBranchOptions.value[index] = [];
    message.warning(err.response?.data?.detail || '未获取到分支列表，您可以直接手动输入分支或 Tag。');
  }
}

async function onTaskServerChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.view_id = null;
  task.job_id = null;
  task.branch = null;
  taskViewOptions.value[index] = [];
  taskJobOptions.value[index] = [];
  taskBranchOptions.value[index] = [];
  const server = servers.value.find((item) => item.id === value);
  if (server && !server.is_active) {
    message.warning('该 Jenkins 实例已被禁用，请先启用后再选择。');
    task.server_id = null;
    return;
  }
  if (value) await loadTaskViews(value, index);
}

async function onTaskViewChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.job_id = null;
  task.branch = null;
  taskJobOptions.value[index] = [];
  taskBranchOptions.value[index] = [];
  if (task.server_id && value) await loadTaskJobs(task.server_id, value, index);
}

async function onTaskJobChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.branch = null;
  taskBranchOptions.value[index] = [];
  
  // Save job_name snapshot
  const options = taskJobOptions.value[index] || [];
  const selectedJob = options.find((opt) => opt.value === value);
  if (selectedJob) {
    task.job_name = selectedJob.label;
  }
  
  if (task.server_id && value) {
    await loadTaskBranches(task.server_id, value, index);
  }
}

function optionLabel(options: SelectOption[], value: number | string | null | undefined) {
  return options.find((option) => option.value === value)?.label || '-';
}

async function submitPlan() {
  const fakePlan: ReleasePlan = {
    id: editingPlanId.value || 0,
    name: wizardForm.value.name,
    type: wizardForm.value.type,
    execute_time: wizardForm.value.execute_time ? new Date(wizardForm.value.execute_time).toISOString() : null,
    interval_minutes: wizardForm.value.interval_minutes || 0,
    pipeline_failure_strategy: wizardForm.value.pipeline_strategy,
    notify_dingtalk: wizardForm.value.notify_dingtalk,
    status: 'WAITING',
    creator_id: 0,
    created_at: new Date().toISOString(),
    preflight_status: 'UNCHECKED',
    preflight_checked_at: null,
    preflight_result: null,
    tasks: wizardForm.value.tasks,
  };
  submitLoading.value = true;
  try {
    const payload = buildPayload(fakePlan);
    const response = wizardMode.value === 'edit' && editingPlanId.value
      ? await request.put(`/release/plans/${editingPlanId.value}`, payload)
      : await request.post('/release/plans', payload);
    message.success(wizardMode.value === 'edit' ? '发布计划已更新。' : '发布计划已创建。');
    showWizard.value = false;
    if (response.data.preflight_status === 'FAILED') {
      selectedPreflight.value = response.data;
      showPreflight.value = true;
    }
    await loadPlans();
  } catch (err: any) {
    message.error(err.message || '提交失败。');
  } finally {
    submitLoading.value = false;
  }
}

async function openEditFromQuery(value: unknown) {
  const id = Number(value);
  if (!id) return;
  let plan = plans.value.find((item) => item.id === id);
  if (!plan) {
    const res = await request.get(`/release/plans/${id}`);
    plan = res.data;
  }
  if (!plan) return;
  await openEditWizard(plan);
  router.replace('/release');
}

watch(() => route.query.edit, (value) => {
  if (value) openEditFromQuery(value);
});

let pollTimer: any = null;

async function fetchPlansSilent() {
  try {
    const res = await request.get('/release/plans');
    plans.value = res.data || [];
  } catch (e) {
    // 静默轮询
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    fetchPlansSilent();
  }, 3500);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(async () => {
  await Promise.all([loadPlans(), loadServers(), checkDingTalkConfig()]);
  if (route.query.edit) await openEditFromQuery(route.query.edit);
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<style scoped>
:deep(.n-form-item-label) {
  position: relative;
  justify-content: flex-end;
}

:deep(.n-form-item-label__asterisk) {
  position: absolute;
  right: -10px;
  top: 50%;
  transform: translateY(-50%);
}

.skeleton-block,
.wizard-section {
  padding: 20px 6px;
}

.wizard-steps {
  margin-bottom: 24px;
  padding: 0 6px;
}

.task-editor {
  display: grid;
  gap: 16px;
}

.schedule-time-control {
  width: 100%;
}

.quick-time-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.task-row {
  padding: 18px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  background: #fafafa;
}

.task-row__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.task-row__header strong {
  font-size: 14px;
  font-weight: 600;
}

.task-row__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.key-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  margin-bottom: 18px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  background: #fafafa;
}

.key-grid > div {
  min-height: 80px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border-right: 1px solid var(--line-soft);
}

.key-grid > div:last-child {
  border-right: 0;
}

.key-grid span {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
}

.key-grid strong {
  color: var(--text);
  font-size: 17px;
  font-weight: 600;
}

.preview-table {
  min-width: 720px;
}

.wizard-actions {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 16px;
}

.wizard-actions > div {
  display: flex;
  gap: 8px;
}
</style>

<style>
.release-time-picker-panel {
  position: relative;
}
.release-time-picker-panel .n-date-picker-footer {
  border-top: none !important;
  padding: 0 !important;
  height: 0 !important;
  overflow: visible !important;
}
.release-time-picker-panel .quick-time-in-footer {
  position: absolute;
  bottom: 8px;
  left: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>



