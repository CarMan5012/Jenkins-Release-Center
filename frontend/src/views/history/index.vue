<template>
  <section class="page history-page">
    <div class="page-header">
      <div>
        <h1>执行历史</h1>
        <p>查询发布记录、构建耗时和 Jenkins Console 日志。</p>
      </div>
      <RefreshButton secondary label="刷新" :loading="headerRefreshLoading" @click="fetchHistories(true, 'header')" />
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

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

    <div class="toolbar" style="display: flex; align-items: center; justify-content: space-between;">
      <div style="display: flex; align-items: center; gap: 12px;">
        <n-input v-model:value="queryJobName" clearable placeholder="搜索 Job 名称" style="width: 280px" @keyup.enter="fetchHistories(true, 'query')" />
        <n-select v-model:value="queryStatus" :options="statusOptions" clearable placeholder="执行状态" style="width: 160px" />
        <RefreshButton secondary label="查询" :loading="queryLoading" @click="fetchHistories(true, 'query')" />
      </div>
      <n-button size="small" type="warning" secondary :loading="resetSeqLoading" @click="confirmResetSequence">
        重置 ID 重新从 #1 开始计算
      </n-button>
    </div>

    <section class="panel">
      <div class="panel__header">
        <h2 class="panel__title">历史记录</h2>
        <span class="muted mono">page {{ page }}</span>
      </div>
      <div v-if="pageLoading" class="skeleton-block"><n-skeleton text :repeat="8" /></div>
      <div v-else class="table-wrap">
        <table class="ops-table history-table">
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Job</th>
              <th scope="col">状态</th>
              <th scope="col">构建</th>
              <th scope="col">触发人</th>
              <th scope="col">耗时</th>
              <th scope="col">时间</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in histories" :key="item.id">
              <td class="mono">#{{ item.id }}</td>
              <td>
                <div class="cell-title">
                  <strong>{{ item.job_name || '-' }}</strong>
                  <span v-if="activeTab === 'system'" class="muted mono">{{ item.branch || '-' }} · plan #{{ item.plan_id }}</span>
                  <span v-else class="muted mono">{{ item.branch || '-' }}</span>
                </div>
              </td>
              <td><StatusBadge :status="item.status" /></td>
              <td class="mono">{{ item.build_number ? `#${item.build_number}` : '-' }}</td>
              <td>{{ formatTriggerBy(item.trigger_by) }}</td>
              <td class="mono">{{ formatDuration(item.duration) }}</td>
              <td class="mono">{{ formatDateTime(item.started_at || item.created_at) }}</td>
              <td>
                <div class="cell-actions">
                  <n-button size="tiny" secondary @click="openLogs(item)">查看日志</n-button>
                  <n-button v-if="activeTab === 'system'" size="tiny" secondary :disabled="!item.plan_id" @click="router.push(`/release/${item.plan_id}`)">详情</n-button>
                </div>
              </td>
            </tr>
            <tr v-if="!histories.length">
              <td colspan="8"><div class="empty-inline">没有执行历史。</div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination-row">
        <n-pagination v-model:page="page" :page-count="pageCount" @update:page="fetchHistories(false)" />
      </div>
    </section>

    <n-modal v-model:show="logModalVisible" preset="card" style="width: min(960px, 94vw)" @after-leave="activeHistory = null">
      <LogViewer :task-id="activeHistory?.task_id" :history-id="activeHistory?.id" :title="activeHistory?.job_name || 'Console 日志'" :build-number="activeHistory?.build_number" />
    </n-modal>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NInput, NModal, NPagination, NSelect, NSkeleton, NTabPane, NTabs, useDialog, useMessage } from 'naive-ui';
import LogViewer from '../../components/LogViewer.vue';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import { wsService } from '../../utils/websocket';
import { formatDateTime, formatDuration, formatTriggerBy } from '../../utils/release-ui';

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
  is_external?: boolean;
}

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();
const pageLoading = ref(false);
const headerRefreshLoading = ref(false);
const queryLoading = ref(false);
const resetSeqLoading = ref(false);

function confirmResetSequence() {
  dialog.warning({
    title: '确认重置历史记录 ID 序号？',
    content: '此操作将清空当前的发布历史与外部构建记录，并重置数据库计数器。重置后，系统产生的新构建记录将重新从 ID #1 开始计算。确定要继续吗？',
    positiveText: '确认重置归零',
    negativeText: '取消',
    onPositiveClick: async () => {
      resetSeqLoading.value = true;
      try {
        const res = await request.post('/history/reset-sequence');
        message.success(res.data?.message || '历史记录 ID 已重置，从 #1 重新开始计算！');
        await fetchHistories(true);
      } catch (err: any) {
        message.error(err.message || '重置历史记录 ID 失败。');
      } finally {
        resetSeqLoading.value = false;
      }
    }
  });
}
const syncing = ref(false);
const error = ref('');
const histories = ref<ReleaseHistory[]>([]);
const queryJobName = ref('');
const queryStatus = ref<string | null>(null);
const page = ref(1);
const pageCount = ref(1);
const limit = 15;
const logModalVisible = ref(false);
const activeHistory = ref<ReleaseHistory | null>(null);
const activeTab = ref<'system' | 'external'>('system');

function handleHistoryUpdate() {
  fetchHistories(false);
}

const statusOptions = [
  { label: '成功', value: 'SUCCESS' },
  { label: '失败', value: 'FAILED' },
];

async function onTabChange() {
  fetchHistories(true);
  if (activeTab.value === 'external') {
    try {
      await request.post('/history/sync', null, {
        params: { job_name: queryJobName.value || undefined }
      });
      await fetchHistories(false);
    } catch {
      // Ignore background sync errors on tab click
    }
  }
}

async function syncExternalHistories() {
  syncing.value = true;
  try {
    await request.post('/history/sync', null, {
      params: { job_name: queryJobName.value || undefined }
    });
    message.success('外部手动构建同步成功');
    await fetchHistories(true);
  } catch (err: any) {
    message.error(err.message || '外部手动构建同步失败');
  } finally {
    syncing.value = false;
  }
}

async function fetchHistories(resetPage = false, trigger?: 'page' | 'header' | 'query') {
  if (resetPage) page.value = 1;
  const buttonLoading = trigger === 'header'
    ? headerRefreshLoading
    : trigger === 'query'
      ? queryLoading
      : null;
  if (buttonLoading) buttonLoading.value = true;
  else pageLoading.value = true;
  error.value = '';
  try {
    const res = await request.get('/history', {
      params: {
        job_name: queryJobName.value || undefined,
        status: queryStatus.value || undefined,
        is_external: activeTab.value === 'external' ? true : false,
        page: page.value,
        limit,
      },
    });
    histories.value = res.data || [];
    pageCount.value = histories.value.length === limit ? page.value + 1 : page.value;
    if (trigger === 'header') {
      message.success('刷新成功');
    } else if (trigger === 'query') {
      message.success('查询成功');
    }
  } catch (err: any) {
    error.value = err.message || '历史记录加载失败';
    message.error(err.message || '刷新失败');
  } finally {
    if (buttonLoading) {
      buttonLoading.value = false;
    } else {
      pageLoading.value = false;
    }
  }
}

function openLogs(item: ReleaseHistory) {
  activeHistory.value = item;
  logModalVisible.value = true;
}

watch(() => route.query.job, (value) => {
  if (typeof value === 'string') {
    queryJobName.value = value;
    fetchHistories(true);
  }
});

onMounted(async () => {
  if (typeof route.query.tab === 'string' && (route.query.tab === 'external' || route.query.tab === 'system')) {
    activeTab.value = route.query.tab;
  }
  if (typeof route.query.job === 'string') queryJobName.value = route.query.job;
  
  if (activeTab.value === 'external') {
    try {
      await new Promise((resolve) => setTimeout(resolve, 800));
      await request.post('/history/sync', null, {
        params: { job_name: queryJobName.value || undefined }
      });
    } catch {
      // Ignore background sync errors on mount
    }
  }
  await fetchHistories(true);
  wsService.on('HISTORY_UPDATE', handleHistoryUpdate);
});

onUnmounted(() => {
  wsService.off('HISTORY_UPDATE', handleHistoryUpdate);
});
</script>

<style scoped>
.skeleton-block {
  padding: 20px;
}

.history-table {
  min-width: 900px;
}

.pagination-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 14px 16px;
  border-top: 1px solid var(--line-soft);
  background: #fafafa;
}
</style>
