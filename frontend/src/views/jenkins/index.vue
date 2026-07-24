<template>
  <section class="page jenkins-page">
    <div class="page-header">
      <div>
        <h1>Jenkins 集成</h1>
        <p>管理实例、同步 View 和 Job，用于发布计划调度。</p>
      </div>
      <n-button type="primary" @click="openServerModal()">添加实例</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

    <section class="server-strip">
      <article
        v-for="server in servers"
        :key="server.id"
        class="server-card panel"
        :class="{ 'server-card--active': selectedServerId === server.id }"
        role="button"
        tabindex="0"
        :aria-pressed="selectedServerId === server.id"
        @click="selectServer(server.id)"
        @keydown.enter="selectServer(server.id)"
        @keydown.space.prevent="selectServer(server.id)"
      >
        <div class="server-card__top">
          <strong>{{ server.name }}</strong>
          <StatusBadge :status="server.is_active ? 'SUCCESS' : 'DISABLED'" />
        </div>
        <p class="mono">{{ server.url }}</p>
        <small>{{ server.description || '无描述' }}</small>
        <div class="server-card__actions" @click.stop>
          <n-button size="tiny" secondary :loading="busyKey === `test-${server.id}`" @click="testConnection(server)">测试</n-button>
          <RefreshButton size="tiny" type="primary" secondary label="同步" :loading="busyKey === `sync-${server.id}` || syncingServers[server.id]" @click="syncServer(server)" />
          <n-button size="tiny" secondary @click="openBackupDrawer(server)">备份</n-button>
          <n-button size="tiny" secondary @click="openServerModal(server)">编辑</n-button>
          <n-button size="tiny" type="error" secondary :loading="busyKey === `delete-${server.id}`" @click="deleteServer(server)">删除</n-button>
        </div>
      </article>
      <button v-if="!servers.length && !loading" class="empty-server panel" @click="openServerModal()">添加 Jenkins 实例</button>
    </section>

    <div class="workspace-grid">
      <section class="panel views-panel">
        <div class="panel__header">
          <h2 class="panel__title">Views</h2>
          <span class="muted mono">{{ views.length }}</span>
        </div>
        <div class="view-list">
          <button v-for="view in views" :key="view.id" :class="{ active: selectedViewId === view.id }" @click="selectView(view.id)">
            <strong>{{ view.name }}</strong>
            <span class="mono">#{{ view.id }}</span>
          </button>
          <div v-if="!views.length" class="empty-inline">暂无 View，请先同步实例。</div>
        </div>
      </section>

      <section class="panel jobs-panel">
        <div class="panel__header">
          <h2 class="panel__title">Job 列表</h2>
          <span class="muted mono">{{ filteredJobs.length }} / {{ jobs.length }}</span>
        </div>
        <div class="job-toolbar">
          <n-input v-model:value="jobSearch" clearable placeholder="搜索 Job" style="width: 280px" />
          <n-select v-model:value="jobStatus" :options="jobStatusOptions" style="width: 150px" />
          <RefreshButton secondary label="刷新" :loading="btnJobsRefreshLoading" :disabled="!selectedViewId" @click="loadJobs('refresh')" />
        </div>
        <div v-if="jobsPageLoading" class="skeleton-block"><n-skeleton text :repeat="8" /></div>

        <div v-else class="table-wrap">
          <table class="ops-table jobs-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">状态</th>
                <th scope="col">最近构建</th>
                <th scope="col">路径</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="job in filteredJobs" :key="job.id">
                <td>
                  <div class="cell-title">
                    <strong>{{ job.name }}</strong>
                    <span class="muted">{{ job.description || '无描述' }}</span>
                  </div>
                </td>
                <td><StatusBadge :status="job.last_build_result || 'NOT_BUILT'" /></td>
                <td class="mono">{{ job.last_build_number ? `#${job.last_build_number}` : '-' }} · {{ formatDateTime(job.last_build_time) }}</td>
                <td class="mono">{{ job.folder || '/' }}</td>
                <td>
                  <div class="cell-actions">
                    <n-button size="tiny" type="primary" @click="openRunModal(job)">运行</n-button>
                    <n-button size="tiny" secondary @click="router.push({ path: '/history', query: { job: job.name } })">查看日志</n-button>
                  </div>
                </td>
              </tr>
              <tr v-if="!filteredJobs.length">
                <td colspan="5"><div class="empty-inline">没有匹配的 Job。</div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <n-modal v-model:show="serverModalVisible" preset="card" :title="editingServerId ? '编辑 Jenkins 实例' : '添加 Jenkins 实例'" style="width: min(640px, 94vw)">
      <n-form ref="serverFormRef" :model="serverForm" :rules="serverRules" label-placement="left" label-width="110">
        <n-form-item label="名称" path="name"><n-input v-model:value="serverForm.name" /></n-form-item>
        <n-form-item label="Base URL" path="url"><n-input v-model:value="serverForm.url" placeholder="http://jenkins.example.com" /></n-form-item>
        <n-form-item label="用户名" path="username"><n-input v-model:value="serverForm.username" /></n-form-item>
        <n-form-item label="API Token" :path="editingServerId ? undefined : 'api_token'">
          <n-input v-model:value="serverForm.api_token" type="password" show-password-on="click" :placeholder="editingServerId ? '留空表示不修改' : '必填'" />
        </n-form-item>
        <n-form-item label="描述"><n-input v-model:value="serverForm.description" type="textarea" /></n-form-item>
        <n-form-item label="启用"><n-switch v-model:value="serverForm.is_active" :checked-value="1" :unchecked-value="0" /></n-form-item>
      </n-form>
      <template #action>
        <div class="modal-actions">
          <n-button secondary @click="serverModalVisible = false">取消</n-button>
          <n-button type="primary" :loading="submitLoading" @click="submitServer">保存</n-button>
        </div>
      </template>
    </n-modal>

    <n-modal v-model:show="runModalVisible" preset="card" title="运行 Job" style="width: min(560px, 94vw)">
      <n-form label-placement="left" label-width="90">
        <n-form-item label="Job"><strong>{{ activeJob?.name }}</strong></n-form-item>
        <n-form-item label="分支/Tag" required>
          <n-select v-model:value="runBranch" :options="branchOptions" filterable tag placeholder="选择或输入分支" />
        </n-form-item>
      </n-form>
      <template #action>
        <div class="modal-actions">
          <n-button secondary @click="runModalVisible = false">取消</n-button>
          <n-button type="primary" :loading="submitLoading" @click="submitQuickRun">运行</n-button>
        </div>
      </template>
    </n-modal>

    <!-- Backup Drawer -->
    <BackupDrawer 
      v-model:show="backupDrawerVisible" 
      :server="backupActiveServer" 
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSkeleton,
  NSwitch,
  useDialog,
  useMessage,
} from 'naive-ui';
import type { FormInst, FormRules } from 'naive-ui';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import BackupDrawer from './BackupDrawer.vue';
import request from '../../utils/request';
import { encryptData } from '../../utils/crypto';
import { formatDateTime, isPreflightBlocked } from '../../utils/release-ui';

interface JenkinsServer {
  id: number;
  name: string;
  url: string;
  username: string;
  description?: string | null;
  is_active: number;
}

interface JenkinsView {
  id: number;
  name: string;
  server_id: number;
}

interface JenkinsJob {
  id: number;
  server_id: number;
  view_id?: number | null;
  name: string;
  folder?: string | null;
  description?: string | null;
  last_build_number?: number | null;
  last_build_result?: string | null;
  last_build_time?: string | null;
}

const router = useRouter();
const message = useMessage();
const dialog = useDialog();
const serverFormRef = ref<FormInst | null>(null);

const loading = ref(false);
const jobsPageLoading = ref(false);
const btnJobsRefreshLoading = ref(false);
const submitLoading = ref(false);
const error = ref('');
const busyKey = ref('');
const servers = ref<JenkinsServer[]>([]);
const views = ref<JenkinsView[]>([]);
const jobs = ref<JenkinsJob[]>([]);
const selectedServerId = ref<number | null>(null);
const selectedViewId = ref<number | null>(null);
const jobSearch = ref('');
const jobStatus = ref('ALL');

const serverModalVisible = ref(false);
const editingServerId = ref<number | null>(null);
const serverForm = ref({ name: '', url: '', username: '', api_token: '', description: '', is_active: 1 });

const runModalVisible = ref(false);
const activeJob = ref<JenkinsJob | null>(null);
const runBranch = ref('');
const branchOptions = ref<Array<{ label: string; value: string }>>([]);

// Backup Drawer states
const backupDrawerVisible = ref(false);
const backupActiveServer = ref<JenkinsServer | null>(null);

function openBackupDrawer(server: JenkinsServer) {
  backupActiveServer.value = server;
  backupDrawerVisible.value = true;
}

// 同步状态轮询管理
const syncingServers = ref<Record<number, boolean>>({});

function pollSyncStatus(serverId: number) {
  if (syncingServers.value[serverId]) return; // 避免重复拉起轮询
  syncingServers.value[serverId] = true;
  
  let attempts = 0;
  const maxAttempts = 30; // 最多轮询 60 秒
  const interval = setInterval(async () => {
    attempts++;
    try {
      const res = await request.get(`/jenkins/servers/${serverId}/sync/status`);
      if (!res.data.syncing) {
        clearInterval(interval);
        delete syncingServers.value[serverId];
        // 如果轮询结束时该服务器依然被选中，则更新其视图数据
        if (selectedServerId.value === serverId) {
          await selectServer(serverId);
        }
        message.success('数据同步已完成。');
      }
    } catch (err) {
      clearInterval(interval);
      delete syncingServers.value[serverId];
    }
    
    if (attempts >= maxAttempts) {
      clearInterval(interval);
      delete syncingServers.value[serverId];
      message.warning('同步已在后台处理，请稍后刷新查看。');
    }
  }, 2000);
}

async function checkAndResumeSyncPolling(serverId: number) {
  try {
    const res = await request.get(`/jenkins/servers/${serverId}/sync/status`);
    if (res.data.syncing) {
      pollSyncStatus(serverId);
    }
  } catch (err) {
    // 忽略异常
  }
}

const serverRules: FormRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  url: [{ required: true, message: '请输入 URL', trigger: 'blur' }],
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  api_token: [{ required: true, message: '请输入 API Token', trigger: 'blur' }],
};

const jobStatusOptions = [
  { label: '全部状态', value: 'ALL' },
  { label: '成功', value: 'SUCCESS' },
  { label: '失败', value: 'FAILURE' },
  { label: '未构建', value: 'NOT_BUILT' },
];

const filteredJobs = computed(() => {
  const text = jobSearch.value.trim().toLowerCase();
  return jobs.value.filter((job) => {
    const status = job.last_build_result || 'NOT_BUILT';
    const matchesText = !text || job.name.toLowerCase().includes(text) || (job.folder || '').toLowerCase().includes(text);
    const matchesStatus = jobStatus.value === 'ALL' || status === jobStatus.value;
    return matchesText && matchesStatus;
  });
});

async function loadServers() {
  loading.value = true;
  error.value = '';
  try {
    const res = await request.get('/jenkins/servers');
    servers.value = res.data || [];
    
    if (!servers.value.length) {
      selectedServerId.value = null;
      selectedViewId.value = null;
      views.value = [];
      jobs.value = [];
    } else {
      const hasSelected = servers.value.some((s) => s.id === selectedServerId.value);
      if (!selectedServerId.value || !hasSelected) {
        await selectServer(servers.value[0].id);
      } else {
        checkAndResumeSyncPolling(selectedServerId.value);
      }
    }
  } catch (err: any) {
    error.value = err.message || 'Jenkins 实例加载失败。';
  } finally {
    loading.value = false;
  }
}

async function selectServer(id: number) {
  selectedServerId.value = id;
  selectedViewId.value = null;
  views.value = [];
  jobs.value = [];
  
  checkAndResumeSyncPolling(id);
  
  try {
    const res = await request.get(`/jenkins/servers/${id}/views`);
    views.value = res.data || [];
    if (views.value.length) await selectView(views.value[0].id);
  } catch (err: any) {
    message.error(err.message || 'View 加载失败。');
  }
}

async function selectView(id: number) {
  selectedViewId.value = id;
  await loadJobs();
}

async function loadJobs(trigger?: 'page' | 'refresh') {
  if (!selectedServerId.value || !selectedViewId.value) return;
  if (trigger === 'refresh') {
    btnJobsRefreshLoading.value = true;
  } else {
    jobsPageLoading.value = true;
  }
  const startTime = Date.now();
  try {
    const res = await request.get(`/jenkins/servers/${selectedServerId.value}/views/${selectedViewId.value}/jobs`);
    jobs.value = res.data || [];
  } catch (err: any) {
    message.error(err.message || 'Job 加载失败。');
  } finally {
    const elapsed = Date.now() - startTime;
    if (elapsed < 500) {
      await new Promise((resolve) => setTimeout(resolve, 500 - elapsed));
    }
    jobsPageLoading.value = false;
    btnJobsRefreshLoading.value = false;
  }
}

async function runServerAction(key: string, action: () => Promise<void>) {
  busyKey.value = key;
  try {
    await action();
    await loadServers();
  } catch (err: any) {
    message.error(err.message || '操作失败。');
  } finally {
    busyKey.value = '';
  }
}

function testConnection(server: JenkinsServer) {
  runServerAction(`test-${server.id}`, async () => {
    const res = await request.post(`/jenkins/servers/${server.id}/test`);
    if (res.data.success) message.success(res.data.message || '连接正常。');
    else message.error(res.data.message || '连接失败。');
  });
}

function syncServer(server: JenkinsServer) {
  runServerAction(`sync-${server.id}`, async () => {
    const res = await request.post(`/jenkins/servers/${server.id}/sync`);
    message.success(res.data.message || '已触发后台同步。');
    pollSyncStatus(server.id);
  });
}

function deleteServer(server: JenkinsServer) {
  dialog.error({
    title: '删除 Jenkins 实例',
    content: `确认删除 ${server.name}？同步的 View 和 Job 也会清理。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => runServerAction(`delete-${server.id}`, async () => {
      await request.delete(`/jenkins/servers/${server.id}`);
      message.success('已删除。');
    }),
  });
}

function openServerModal(server?: JenkinsServer) {
  editingServerId.value = server?.id || null;
  serverForm.value = server ? {
    name: server.name,
    url: server.url,
    username: server.username,
    api_token: '',
    description: server.description || '',
    is_active: server.is_active,
  } : { name: '', url: '', username: '', api_token: '', description: '', is_active: 1 };
  serverModalVisible.value = true;
}

function submitServer() {
  serverFormRef.value?.validate(async (errors) => {
    if (errors) return;
    submitLoading.value = true;
    try {
      const payload: any = { ...serverForm.value };
      if (payload.api_token) {
        payload.api_token = await encryptData(payload.api_token);
      } else if (editingServerId.value) {
        delete payload.api_token;
      }
      if (editingServerId.value) {
        await request.put(`/jenkins/servers/${editingServerId.value}`, payload);
        message.success('实例已更新。');
      } else {
        await request.post('/jenkins/servers', payload);
        message.success('实例已创建。');
      }
      serverModalVisible.value = false;
      await loadServers();
    } catch (err: any) {
      message.error(err.message || '保存失败。');
    } finally {
      submitLoading.value = false;
    }
  });
}

async function openRunModal(job: JenkinsJob) {
  activeJob.value = job;
  runBranch.value = '';
  branchOptions.value = [];
  runModalVisible.value = true;
  try {
    const res = await request.get(`/jenkins/servers/${job.server_id}/jobs/${job.id}/branches`);
    branchOptions.value = (res.data || []).map((branch: string) => ({ label: branch, value: branch }));
    runBranch.value = branchOptions.value[0]?.value || '';
  } catch (err: any) {
    message.warning(err.message || '分支加载失败，可手动输入。');
  }
}

async function submitQuickRun() {
  if (!activeJob.value || !runBranch.value) {
    message.error('请填写分支或 Tag。');
    return;
  }
  submitLoading.value = true;
  try {
    const response = await request.post('/release/plans', {
      name: `Run ${activeJob.value.name}`,
      type: 'IMMEDIATE',
      execute_time: null,
      interval_minutes: 0,
      pipeline_failure_strategy: 'STOP',
      tasks: [{
        server_id: activeJob.value.server_id,
        job_id: activeJob.value.id,
        branch: runBranch.value,
        parameters: {},
        sequence: 0,
        depends_on_sequence: null,
      }],
    });
    const status = response.data.preflight_status;
    if (isPreflightBlocked(status)) {
      message.error('计划已创建，但发布前检查未通过，请到发布计划页面处理');
      return;
    }
    if (status === 'WARNING') {
      message.warning('计划已创建，预检存在警告，请到发布计划页面确认后运行');
      return;
    }
    message.success('已创建立即执行任务。');
    runModalVisible.value = false;
    router.push('/release');
  } catch (err: any) {
    message.error(err.message || '运行失败。');
  } finally {
    submitLoading.value = false;
  }
}

onMounted(loadServers);
</script>

<style scoped>
.server-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.server-card,
.empty-server {
  min-height: 152px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.empty-server {
  display: grid;
  place-items: center;
  border: 1px dashed var(--line);
  background: rgba(255, 255, 255, 0.56);
  color: var(--text-faint);
  font-weight: 500;
}

.empty-server:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.server-card--active {
  border-color: rgba(0, 113, 227, 0.38);
  background: var(--primary-soft);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.08);
}

.server-card:active,
.empty-server:active {
  transform: scale(0.992);
}

.server-card__top,
.server-card__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.server-card__top strong {
  font-size: 15px;
  font-weight: 600;
}

.server-card p {
  margin: 12px 0 6px;
  overflow-wrap: anywhere;
  color: var(--text-muted);
  font-size: 12.5px;
}

.server-card small {
  display: block;
  min-height: 20px;
  color: var(--text-faint);
  font-size: 11.5px;
}

.server-card__actions {
  justify-content: flex-start;
  flex-wrap: wrap;
  margin-top: 14px;
}

.workspace-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 16px;
}

.view-list {
  display: grid;
  gap: 4px;
  padding: 10px;
}

.view-list button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  min-height: 38px;
  padding: 0 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-muted);
  text-align: left;
  cursor: pointer;
  font-weight: 500;
  transition: color 0.16s ease, background 0.16s ease, transform 0.16s ease;
}

.view-list button strong {
  font-weight: 500;
}

.view-list button:hover {
  background: var(--surface-subtle);
  color: var(--text);
}

.view-list button:active {
  transform: scale(0.985);
}

.view-list button.active {
  background: var(--primary-soft);
  color: var(--text);
  font-weight: 600;
}

.view-list button.active strong {
  font-weight: 600;
}

.job-toolbar {
  display: flex;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
  flex-wrap: wrap;
}

.skeleton-block {
  padding: 20px;
}

.jobs-table {
  min-width: 840px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
