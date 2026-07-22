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
              <th scope="col">类型</th>
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
                  <span class="muted mono">#{{ plan.id }} · 创建 {{ formatDateTime(plan.created_at) }}</span>
                </RouterLink>
              </td>
              <td><StatusBadge :status="plan.status" /></td>
              <td>{{ formatPlanType(plan.type) }}</td>
              <td class="mono">{{ formatDateTime(plan.execute_time) }}</td>
              <td class="mono">{{ getWeekDay(plan.execute_time) }}</td>
              <td class="mono">{{ plan.tasks?.length || 0 }}</td>
              <td>
                <div class="cell-actions">
                  <n-button size="tiny" type="primary" :loading="busyKey === `run-${plan.id}`" :disabled="plan.status !== 'WAITING'" @click="triggerPlan(plan)">运行</n-button>
                  <n-button size="tiny" type="warning" secondary :loading="busyKey === `cancel-${plan.id}`" :disabled="!['WAITING', 'RUNNING'].includes(plan.status)" @click="cancelPlan(plan)">停止</n-button>
                  <n-button size="tiny" secondary :loading="busyKey === `retry-${plan.id}`" :disabled="['WAITING', 'RUNNING'].includes(plan.status)" @click="retryPlan(plan)">重试</n-button>
                  <n-button size="tiny" secondary :disabled="plan.status !== 'WAITING'" @click="openEditWizard(plan)">编辑</n-button>
                  <n-button size="tiny" secondary @click="$router.push(`/release/${plan.id}`)">日志</n-button>
                  <n-button size="tiny" type="error" secondary :loading="busyKey === `delete-${plan.id}`" :disabled="plan.status === 'RUNNING'" @click="deletePlan(plan)">删除</n-button>
                </div>
              </td>
            </tr>
            <tr v-if="!visiblePlans.length">
              <td colspan="7"><div class="empty-inline">没有发布计划。</div></td>
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
        <n-form label-placement="left" label-width="110">
          <n-form-item label="计划名称" required>
            <n-input v-model:value="wizardForm.name" placeholder="例如 prod-api-2026.07.09" />
          </n-form-item>
          <n-form-item label="执行类型" required>
            <n-select v-model:value="wizardForm.type" :options="typeOptions.filter((item) => item.value !== 'ALL')" />
          </n-form-item>
          <n-form-item v-if="wizardForm.type !== 'IMMEDIATE'" label="调度时间" required>
            <n-date-picker v-model:value="wizardForm.execute_time" type="datetime" clearable style="width: 100%" />
          </n-form-item>
          <n-form-item v-if="wizardForm.type === 'BATCH'" label="批次间隔">
            <n-input-number v-model:value="wizardForm.interval_minutes" :min="1" style="width: 180px" />
          </n-form-item>
          <n-form-item v-if="wizardForm.type === 'PIPELINE'" label="失败策略">
            <n-select v-model:value="wizardForm.pipeline_strategy" :options="strategyOptions" />
          </n-form-item>
        </n-form>
      </div>

      <div v-if="currentStep === 2" class="wizard-section task-editor">
        <div v-for="(task, index) in wizardForm.tasks" :key="index" class="task-row">
          <div class="task-row__header">
            <strong>任务 #{{ index + 1 }}</strong>
            <n-button v-if="wizardForm.tasks.length > 1" size="tiny" type="error" secondary @click="removeTaskRow(index)">删除</n-button>
          </div>
          <div class="task-row__grid">
            <n-select v-model:value="task.server_id" :options="serverOptions" placeholder="Jenkins 实例" @update:value="(value) => onTaskServerChange(Number(value), index)" />
            <n-select v-model:value="task.view_id" :options="taskViewOptions[index] || []" placeholder="View" @update:value="(value) => onTaskViewChange(Number(value), index)" />
            <n-select v-model:value="task.job_id" :options="taskJobOptions[index] || []" placeholder="Job" @update:value="(value) => onTaskJobChange(Number(value), index)" />
            <n-select v-model:value="task.branch" :options="taskBranchOptions[index] || []" placeholder="分支/Tag" filterable tag />
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
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
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
  useDialog,
  useMessage,
} from 'naive-ui';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import { filterPlans, formatDateTime, formatPlanType, sortPlans, getWeekDay } from '../../utils/release-ui';

type SelectOption = { label: string; value: number | string };

interface ReleaseTask {
  id?: number;
  server_id: number | null;
  view_id?: number | null;
  job_id: number | null;
  job_name?: string;
  branch: string;
  parameters?: Record<string, unknown>;
  sequence?: number;
  depends_on_task_id?: number | null;
  depends_on_sequence?: number | null;
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
const servers = ref<Array<{ id: number; name: string }>>([]);
const keyword = ref('');
const statusFilter = ref('ALL');
const typeFilter = ref('ALL');
const sortKey = ref('created_desc');

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
  type: 'IMMEDIATE',
  execute_time: null as number | null,
  interval_minutes: 2,
  pipeline_strategy: 'STOP',
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

function emptyTask(): ReleaseTask {
  return {
    server_id: null,
    view_id: null,
    job_id: null,
    branch: '',
    depends_on_task_id: null,
  };
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
  runAction(`run-${plan.id}`, async () => {
    await request.post(`/release/plans/${plan.id}/trigger`);
    message.success('已触发执行。');
  });
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

function retryPlan(plan: ReleasePlan) {
  if (!plan.tasks.length) {
    message.warning('该计划没有可重试任务。');
    return;
  }
  runAction(`retry-${plan.id}`, async () => {
    const retryType = plan.type === 'PIPELINE' ? 'PIPELINE' : 'IMMEDIATE';
    const payload = buildPayload(plan, `Retry ${plan.name}`, retryType);
    if (retryType === 'PIPELINE') {
      payload.execute_time = new Date(Date.now() + 5 * 60_000).toISOString();
    }
    const response = await request.post('/release/plans', payload);
    if (retryType === 'PIPELINE') {
      await request.post(`/release/plans/${response.data.id}/trigger`);
    }
    message.success('已创建重试任务。');
  });
}

function buildPayload(plan: ReleasePlan, name = plan.name, type = plan.type) {
  return {
    name,
    type,
    execute_time: type === 'IMMEDIATE' ? null : plan.execute_time,
    interval_minutes: plan.interval_minutes || 0,
    pipeline_failure_strategy: plan.pipeline_failure_strategy || 'STOP',
    tasks: plan.tasks.map((task, index) => ({
      server_id: task.server_id,
      job_id: task.job_id,
      job_name: task.job_name,
      branch: task.branch,
      parameters: task.parameters || {},
      sequence: index,
      depends_on_sequence: type === 'PIPELINE' && index > 0 ? index - 1 : null,
    })),
  };
}

function openCreateWizard() {
  wizardMode.value = 'create';
  editingPlanId.value = null;
  currentStep.value = 1;
  taskViewOptions.value = {};
  taskJobOptions.value = {};
  taskBranchOptions.value = {};
  wizardForm.value = {
    name: '',
    type: 'IMMEDIATE',
    execute_time: null,
    interval_minutes: 2,
    pipeline_strategy: 'STOP',
    tasks: [emptyTask()],
  };
  if (servers.value.length === 1) {
    wizardForm.value.tasks[0].server_id = servers.value[0].id;
    onTaskServerChange(servers.value[0].id, 0);
  }
  showWizard.value = true;
}

async function openEditWizard(plan: ReleasePlan) {
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
    const invalidIndex = wizardForm.value.tasks.findIndex((task) => !task.server_id || !task.view_id || !task.job_id || !task.branch);
    if (invalidIndex >= 0) {
      message.error(`任务 #${invalidIndex + 1} 未配置完整。`);
      return;
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
  const res = await request.get(`/jenkins/servers/${serverId}/jobs/${jobId}/branches`);
  taskBranchOptions.value[index] = (res.data || []).map((branch: string) => ({ label: branch, value: branch }));
}

async function onTaskServerChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.view_id = null;
  task.job_id = null;
  task.branch = '';
  taskViewOptions.value[index] = [];
  taskJobOptions.value[index] = [];
  taskBranchOptions.value[index] = [];
  if (value) await loadTaskViews(value, index);
}

async function onTaskViewChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.job_id = null;
  task.branch = '';
  taskJobOptions.value[index] = [];
  taskBranchOptions.value[index] = [];
  if (task.server_id && value) await loadTaskJobs(task.server_id, value, index);
}

async function onTaskJobChange(value: number, index: number) {
  const task = wizardForm.value.tasks[index];
  task.branch = '';
  taskBranchOptions.value[index] = [];
  
  // Save job_name snapshot
  const options = taskJobOptions.value[index] || [];
  const selectedJob = options.find((opt) => opt.value === value);
  if (selectedJob) {
    task.job_name = selectedJob.label;
  }
  
  if (task.server_id && value) {
    await loadTaskBranches(task.server_id, value, index);
    const first = taskBranchOptions.value[index]?.[0]?.value;
    if (typeof first === 'string') task.branch = first;
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
    status: 'WAITING',
    creator_id: 0,
    created_at: new Date().toISOString(),
    tasks: wizardForm.value.tasks,
  };
  submitLoading.value = true;
  try {
    const payload = buildPayload(fakePlan);
    if (wizardMode.value === 'edit' && editingPlanId.value) {
      await request.put(`/release/plans/${editingPlanId.value}`, payload);
      message.success('发布计划已更新。');
    } else {
      await request.post('/release/plans', payload);
      message.success('发布计划已创建。');
    }
    showWizard.value = false;
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

onMounted(async () => {
  await Promise.all([loadPlans(), loadServers()]);
  if (route.query.edit) await openEditFromQuery(route.query.edit);
});
</script>

<style scoped>
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



