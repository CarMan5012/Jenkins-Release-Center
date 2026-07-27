<template>
  <section class="page config-page">
    <div class="page-header">
      <div>
        <h1>系统配置</h1>
        <p>通知渠道和安全审计记录。</p>
      </div>
    </div>

    <n-tabs type="segment" animated>
      <n-tab-pane name="notify" tab="通知渠道">
        <section class="panel">
          <div class="panel__header">
            <h2 class="panel__title">通知渠道</h2>
            <n-button size="small" type="primary" @click="openNotifyModal()">添加渠道</n-button>
          </div>
          <n-alert v-if="notifyError" type="error" :bordered="false" class="tab-alert">{{ notifyError }}</n-alert>
          <div v-if="notifyLoading" class="skeleton-block"><n-skeleton text :repeat="6" /></div>
          <div v-else class="channel-grid">
            <article v-for="item in notifyConfigs" :key="item.id" class="channel-card">
              <div class="channel-card__top">
                <strong>{{ item.name }}</strong>
                <StatusBadge :status="item.is_active ? 'SUCCESS' : 'DISABLED'" />
              </div>
              <div class="channel-meta">
                <span>{{ channelLabel(item.channel_type) }}</span>
                <code class="mono" :title="item.webhook_url">{{ maskWebhook(item.webhook_url) }}</code>
              </div>
              <div class="event-tags" style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                <span class="muted" style="font-size: 13px;">触发事件：</span>
                <n-tag v-for="event in item.trigger_events" :key="event" size="small" :bordered="false">{{ eventLabel(event) }}</n-tag>
              </div>
              <div class="channel-actions">
                <n-button size="tiny" type="primary" secondary :loading="busyKey === `test-${item.id}`" @click="testNotify(item)">测试</n-button>
                <n-button size="tiny" secondary @click="openNotifyModal(item)">编辑</n-button>
                <n-button size="tiny" type="error" secondary :loading="busyKey === `delete-${item.id}`" @click="deleteNotify(item)">删除</n-button>
              </div>
            </article>
            <div v-if="!notifyConfigs.length" class="empty-inline">未配置通知渠道。</div>
          </div>
        </section>
      </n-tab-pane>

      <n-tab-pane name="audit" tab="安全审计">
        <section class="panel">
          <div class="panel__header">
            <h2 class="panel__title">审计日志</h2>
            <span class="muted mono">page {{ auditPage }}</span>
          </div>
          <div class="audit-toolbar">
            <n-input v-model:value="searchUsername" clearable placeholder="过滤用户名" style="width: 240px" @keyup.enter="loadAuditLogs(true)" />
            <RefreshButton secondary label="查询" :loading="auditLoading" @click="loadAuditLogs(true)" />
          </div>
          <n-alert v-if="auditError" type="error" :bordered="false" class="tab-alert">{{ auditError }}</n-alert>
          <div v-if="auditLoading" class="skeleton-block"><n-skeleton text :repeat="8" /></div>
          <div v-else class="table-wrap">
            <table class="ops-table audit-table">
              <thead>
                <tr>
                  <th scope="col">ID</th>
                  <th scope="col">用户</th>
                  <th scope="col">动作</th>
                  <th scope="col">IP</th>
                  <th scope="col">详情</th>
                  <th scope="col">时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="log in auditLogs" :key="log.id">
                  <td class="mono">#{{ log.id }}</td>
                  <td>{{ log.username }}</td>
                  <td><n-tag size="small" :bordered="false">{{ translateAction(log.action) }}</n-tag></td>
                  <td class="mono">{{ log.ip_address || 'unknown' }}</td>
                  <td>{{ translateDetails(log.action, log.details) }}</td>
                  <td class="mono">{{ formatDateTime(log.created_at) }}</td>
                </tr>
                <tr v-if="!auditLogs.length">
                  <td colspan="6"><div class="empty-inline">暂无审计数据。</div></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pagination-row">
            <n-pagination v-model:page="auditPage" :page-count="auditPageCount" @update:page="loadAuditLogs(false)" />
          </div>
        </section>
      </n-tab-pane>

      <n-tab-pane name="retention" tab="数据保留策略">
        <section class="panel">
          <div class="panel__header">
            <h2 class="panel__title">数据保留策略</h2>
          </div>
          <div style="padding: 24px 28px; max-width: 620px;">
            <n-form label-placement="left" label-width="180">
              <n-form-item label="安全审计日志保留" feedback="设定安全审计日志的最大保留天数。设为 0 或留空则永久保留。">
                <n-input-number v-model:value="auditRetention" :min="0" placeholder="默认 30 天，0 表示永久">
                  <template #suffix>天</template>
                </n-input-number>
              </n-form-item>
              <n-form-item label="执行历史与日志保留" feedback="设定构建发布历史及日志缓存的最大保留天数。设为 0 或留空则永久保留。">
                <n-input-number v-model:value="historyRetention" :min="0" placeholder="默认 30 天，0 表示永久">
                  <template #suffix>天</template>
                </n-input-number>
              </n-form-item>
              <n-form-item label="计划列表保留" feedback="只清理已结束计划；等待中和运行中计划不会删除。设为 0 则永久保留。">
                <n-input-number v-model:value="planRetention" :min="0" placeholder="默认 30 天，0 表示永久">
                  <template #suffix>天</template>
                </n-input-number>
              </n-form-item>
              <div style="margin-top: 32px; display: flex; justify-content: flex-end;">
                <n-button type="primary" :loading="submitRetentionLoading" @click="submitRetentionPolicy">
                  保存策略
                </n-button>
              </div>
            </n-form>

            <n-divider style="margin: 32px 0 24px 0;" />

            <div class="reset-sequence-block">
              <h3 style="font-size: 15px; font-weight: 600; margin-bottom: 8px;">历史记录 ID 序号重置</h3>
              <p class="muted" style="font-size: 13px; margin-bottom: 16px; line-height: 1.6;">
                清空当前的发布历史与外部构建记录，将数据库自增主键序号彻底归零重置。重置后，系统产生的新构建记录将重新从 <strong>ID #1</strong> 开始计算。
              </p>
              <n-button type="warning" secondary :loading="resetSeqLoading" @click="confirmResetSequence">
                ID 重新从 #1 开始计算
              </n-button>
            </div>
          </div>
        </section>
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="notifyModalVisible" preset="card" :title="editingNotifyId ? '编辑通知渠道' : '添加通知渠道'" style="width: min(640px, 94vw)">
      <n-form ref="notifyFormRef" :model="notifyForm" :rules="notifyRules" label-placement="left" label-width="110">
        <n-form-item label="渠道名称" path="name"><n-input v-model:value="notifyForm.name" /></n-form-item>
        <n-form-item label="渠道类型" path="channel_type"><n-select v-model:value="notifyForm.channel_type" :options="channelOptions" /></n-form-item>
        <n-form-item label="Webhook" path="webhook_url">
          <n-input v-model:value="notifyForm.webhook_url" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="Secret"><n-input v-model:value="notifyForm.secret" type="password" show-password-on="click" /></n-form-item>
        <n-form-item v-if="notifyForm.channel_type === 'DINGTALK'" label="关键词">
          <n-input v-model:value="notifyForm.keyword" placeholder="钉钉自定义安全关键词" />
        </n-form-item>
        <n-form-item label="启用"><n-switch v-model:value="notifyForm.is_active" :checked-value="1" :unchecked-value="0" /></n-form-item>
        <n-form-item label="触发事件" path="trigger_events">
          <n-checkbox-group v-model:value="notifyForm.trigger_events">
            <n-space>
              <n-checkbox value="start" label="开始" />
              <n-checkbox value="success" label="成功" />
              <n-checkbox value="failed" label="失败" />
            </n-space>
          </n-checkbox-group>
        </n-form-item>
      </n-form>
      <template #action>
        <div class="modal-actions">
          <n-button secondary @click="notifyModalVisible = false">取消</n-button>
          <n-button type="primary" :loading="submitLoading" @click="submitNotify">保存</n-button>
        </div>
      </template>
    </n-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NPagination,
  NSelect,
  NSkeleton,
  NSpace,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui';
import type { FormInst, FormRules } from 'naive-ui';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import request from '../../utils/request';
import { encryptData } from '../../utils/crypto';
import { formatDateTime } from '../../utils/release-ui';

interface NotifyConfig {
  id: number;
  name: string;
  channel_type: string;
  webhook_url: string;
  secret?: string | null;
  keyword?: string | null;
  is_active: number;
  trigger_events: string[];
}

interface AuditLog {
  id: number;
  username: string;
  action: string;
  ip_address?: string | null;
  details?: string | null;
  created_at: string;
}

const message = useMessage();
const dialog = useDialog();
const notifyFormRef = ref<FormInst | null>(null);

const notifyLoading = ref(false);
const auditLoading = ref(false);
const submitLoading = ref(false);
const notifyError = ref('');
const auditError = ref('');
const busyKey = ref('');
const notifyConfigs = ref<NotifyConfig[]>([]);
const auditLogs = ref<AuditLog[]>([]);
const searchUsername = ref('');
const auditPage = ref(1);
const auditLimit = 20;
const notifyModalVisible = ref(false);
const editingNotifyId = ref<number | null>(null);

const notifyForm = ref({
  name: '',
  channel_type: 'DINGTALK',
  webhook_url: '',
  secret: '',
  keyword: '',
  is_active: 1,
  trigger_events: ['start', 'success', 'failed'] as string[],
});

const notifyRules: FormRules = {
  name: [{ required: true, message: '请输入渠道名称', trigger: 'blur' }],
  webhook_url: [{ required: true, message: '请输入 Webhook', trigger: 'blur' }],
  trigger_events: [{ type: 'array', required: true, message: '至少选择一个事件', trigger: 'change' }],
};

const channelOptions = [
  { label: '钉钉', value: 'DINGTALK' },
  { label: '企业微信', value: 'WECHAT' },
  { label: '通用 Webhook', value: 'WEBHOOK' },
];

const auditPageCount = computed(() => auditLogs.value.length === auditLimit ? auditPage.value + 1 : auditPage.value);

function channelLabel(value: string) {
  return channelOptions.find((item) => item.value === value)?.label || value;
}

function eventLabel(value: string) {
  const mapping: Record<string, string> = { start: '开始', success: '成功', failed: '失败' };
  return mapping[value] || value;
}

async function loadNotifyConfigs() {
  notifyLoading.value = true;
  notifyError.value = '';
  try {
    const res = await request.get('/system/notify-configs');
    notifyConfigs.value = res.data || [];
  } catch (err: any) {
    notifyError.value = err.message || '通知配置加载失败。';
  } finally {
    notifyLoading.value = false;
  }
}

async function loadAuditLogs(resetPage = false) {
  if (resetPage) auditPage.value = 1;
  auditLoading.value = true;
  const startedAt = Date.now();
  auditError.value = '';
  try {
    const res = await request.get('/system/audit-logs', {
      params: {
        username: searchUsername.value || undefined,
        page: auditPage.value,
        limit: auditLimit,
      },
    });
    auditLogs.value = res.data || [];
  } catch (err: any) {
    auditError.value = err.message || '审计日志加载失败。';
  } finally {
    if (resetPage) {
      const remaining = 500 - (Date.now() - startedAt);
      if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
    }
    auditLoading.value = false;
  }
}

function openNotifyModal(item?: NotifyConfig) {
  editingNotifyId.value = item?.id || null;
  notifyForm.value = item ? {
    name: item.name,
    channel_type: item.channel_type,
    webhook_url: item.webhook_url,
    secret: item.secret || '',
    keyword: item.keyword || '',
    is_active: item.is_active,
    trigger_events: [...item.trigger_events],
  } : {
    name: '',
    channel_type: 'DINGTALK',
    webhook_url: '',
    secret: '',
    keyword: '',
    is_active: 1,
    trigger_events: ['start', 'success', 'failed'],
  };
  notifyModalVisible.value = true;
}

function submitNotify() {
  notifyFormRef.value?.validate(async (errors) => {
    if (errors) return;
    submitLoading.value = true;
    try {
      const payload = { ...notifyForm.value };
      if (payload.secret) {
        payload.secret = await encryptData(payload.secret);
      }
      if (payload.webhook_url) {
        payload.webhook_url = await encryptData(payload.webhook_url);
      }
      if (editingNotifyId.value) {
        await request.put(`/system/notify-configs/${editingNotifyId.value}`, payload);
        message.success('通知渠道已更新');
      } else {
        await request.post('/system/notify-configs', payload);
        message.success('通知渠道已创建');
      }
      notifyModalVisible.value = false;
      await loadNotifyConfigs();
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      submitLoading.value = false;
    }
  });
}

async function testNotify(item: NotifyConfig) {
  busyKey.value = `test-${item.id}`;
  try {
    const res = await request.post(`/system/notify-configs/${item.id}/test`);
    message.success(res.data.message || '测试消息已成功发出，请检查接收端');
  } catch (err: any) {
    message.error(err.message || '测试消息发送失败');
  } finally {
    busyKey.value = '';
  }
}

/**
 * Webhook URL 敏感参数及 Token 脱敏显示
 */
function maskWebhook(url: string): string {
  if (!url) return '';
  try {
    const u = new URL(url);
    if (u.search) {
      const searchParams = new URLSearchParams(u.search);
      let changed = false;
      searchParams.forEach((value, key) => {
        // 对长度较大的 Token 进行前六位后六位保留、中间打码遮掩
        if (value.length > 10) {
          searchParams.set(key, value.substring(0, 6) + '••••••••' + value.substring(value.length - 6));
          changed = true;
        }
      });
      if (changed) {
        return `${u.protocol}//${u.host}${u.pathname}?${searchParams.toString()}`;
      }
    }
  } catch (e) {}
  // 通用备用打码
  if (url.length > 35) {
    return url.substring(0, 25) + '••••••••';
  }
  return url;
}

function deleteNotify(item: NotifyConfig) {
  dialog.error({
    title: '删除通知渠道',
    content: `确认删除 ${item.name}？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      busyKey.value = `delete-${item.id}`;
      try {
        await request.delete(`/system/notify-configs/${item.id}`);
        message.success('已删除。');
        await loadNotifyConfigs();
      } catch (err: any) {
        message.error(err.message || '删除失败。');
      } finally {
        busyKey.value = '';
      }
    },
  });
}

const auditRetention = ref<number | null>(30);
const historyRetention = ref<number | null>(30);
const planRetention = ref<number | null>(30);
const submitRetentionLoading = ref(false);

async function loadSystemConfigs() {
  try {
    const res = await request.get('/system/configs');
    const configs = res.data || [];
    
    const auditDays = configs.find((c: any) => c.config_key === 'audit_log_retention_days');
    if (auditDays) {
      auditRetention.value = parseInt(auditDays.config_value) ?? 0;
    } else {
      auditRetention.value = 0;
    }

    const historyDays = configs.find((c: any) => c.config_key === 'history_retention_days');
    if (historyDays) {
      historyRetention.value = parseInt(historyDays.config_value) ?? 0;
    } else {
      historyRetention.value = 0;
    }

    const planDays = configs.find((c: any) => c.config_key === 'plan_retention_days');
    planRetention.value = planDays ? parseInt(planDays.config_value) : 30;
  } catch (err: any) {
    message.error(err.message || '加载系统配置失败');
  }
}

async function submitRetentionPolicy() {
  submitRetentionLoading.value = true;
  try {
    await Promise.all([
      request.post('/system/configs', {
        config_key: 'audit_log_retention_days',
        config_value: String(auditRetention.value || 0),
        description: '安全审计日志保留天数 (天，0表示永久保留)'
      }),
      request.post('/system/configs', {
        config_key: 'history_retention_days',
        config_value: String(historyRetention.value || 0),
        description: '执行历史与日志保留天数 (天，0表示永久保留)'
      }),
      request.post('/system/configs', {
        config_key: 'plan_retention_days',
        config_value: String(planRetention.value ?? 30),
        description: '计划列表保留天数 (天，0表示永久保留)'
      })
    ]);
    message.success('数据保留策略保存成功');
  } catch (err: any) {
    message.error(err.message || '数据保留策略保存失败');
  } finally {
    submitRetentionLoading.value = false;
  }
}

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
      } catch (err: any) {
        message.error(err.message || '重置历史记录 ID 失败。');
      } finally {
        resetSeqLoading.value = false;
      }
    }
  });
}

onMounted(() => {
  loadNotifyConfigs();
  loadAuditLogs();
  loadSystemConfigs();
});

const actionMap: Record<string, string> = {
  'LOGIN': '登录',
  'LOGOUT': '退出登录',
  'CREATE_JENKINS_SERVER': '创建 Jenkins 服务',
  'UPDATE_JENKINS_SERVER': '更新 Jenkins 服务',
  'DELETE_JENKINS_SERVER': '删除 Jenkins 服务',
  'SYNC_JENKINS': '同步 Jenkins 视图',
  'IDEMPOTENCY_CONFLICT': '幂等性冲突',
  'CREATE_BACKUP': '创建备份任务',
  'VIEW_BACKUP_DETAILS': '查看备份详情',
  'DOWNLOAD_BACKUP': '下载备份文件',
  'CREATE_RELEASE_PLAN': '创建发布计划',
  'CANCEL_RELEASE_PLAN': '取消发布计划',
  'TRIGGER_RELEASE_PLAN': '手动触发发布',
  'DELETE_RELEASE_PLAN': '删除发布计划',
  'UPDATE_RELEASE_PLAN': '更新发布计划',
  'CREATE_NOTIFY_CONFIG': '创建通知渠道',
  'UPDATE_NOTIFY_CONFIG': '更新通知渠道',
  'DELETE_NOTIFY_CONFIG': '删除通知渠道',
  'TEST_NOTIFY_CONFIG': '测试通知渠道',
  'SET_SYSTEM_CONFIG': '配置系统参数'
};

function translateAction(action: string): string {
  return actionMap[action] || action;
}

function translateDetails(action: string, details: string | null | undefined): string {
  if (!details) return '-';
  
  if (action === 'LOGIN') {
    if (details.includes('Logged in')) return '登录成功';
    if (details.includes('Login failed')) return '登录失败';
  }
  
  if (action === 'CREATE_JENKINS_SERVER') {
    const match = details.match(/Created Jenkins Server (.+)/);
    if (match) return `创建 Jenkins 服务: ${match[1]}`;
  }
  if (action === 'UPDATE_JENKINS_SERVER') {
    const match = details.match(/Updated Jenkins Server (.+)/);
    if (match) return `更新 Jenkins 服务: ${match[1]}`;
  }
  if (action === 'DELETE_JENKINS_SERVER') {
    const match = details.match(/Deleted Jenkins Server (.+)/);
    if (match) return `删除 Jenkins 服务: ${match[1]}`;
  }
  if (action === 'SYNC_JENKINS') {
    const match = details.match(/Synced Views\/Jobs for server (.+)/);
    if (match) return `同步服务 ${match[1]} 的视图与任务`;
  }
  
  if (action === 'CREATE_BACKUP') {
    const match = details.match(/Created backup task (\d+) for server (.+)/);
    if (match) return `为服务 ID ${match[2]} 创建备份任务 #${match[1]}`;
  }
  if (action === 'VIEW_BACKUP_DETAILS') {
    const match = details.match(/Viewed backup details for backup (.+) on server (.+)/);
    if (match) return `查看服务 ID ${match[2]} 的备份 #${match[1]} 详情`;
  }
  if (action === 'DOWNLOAD_BACKUP') {
    const match = details.match(/Downloaded encrypted backup (.+) for server (.+)/);
    if (match) return `下载服务 ID ${match[2]} 的加密备份 #${match[1]}`;
  }
  
  if (action === 'CREATE_RELEASE_PLAN') {
    const match = details.match(/Created release plan:\s*(.+)/);
    if (match) return `创建发布计划: ${match[1]}`;
  }
  if (action === 'CANCEL_RELEASE_PLAN') {
    const match = details.match(/Cancelled release plan:\s*(.+)/);
    if (match) return `取消发布计划: ${match[1]}`;
  }
  if (action === 'TRIGGER_RELEASE_PLAN') {
    const match = details.match(/Triggered release plan early:\s*(.+)/);
    if (match) return `提前触发发布计划: ${match[1]}`;
  }
  if (action === 'DELETE_RELEASE_PLAN') {
    const match = details.match(/Deleted release plan ID:\s*(.+)/);
    if (match) return `删除发布计划 ID: ${match[1]}`;
  }
  if (action === 'UPDATE_RELEASE_PLAN') {
    const match = details.match(/Updated release plan:\s*(.+)/);
    if (match) return `更新发布计划: ${match[1]}`;
  }
  
  if (action === 'CREATE_NOTIFY_CONFIG') {
    const match = details.match(/Created Notification Channel (.+)/);
    if (match) return `创建通知渠道: ${match[1]}`;
  }
  if (action === 'UPDATE_NOTIFY_CONFIG') {
    const match = details.match(/Updated Notification Channel (.+)/);
    if (match) return `更新通知渠道: ${match[1]}`;
  }
  if (action === 'DELETE_NOTIFY_CONFIG') {
    const match = details.match(/Deleted Notification Channel (.+)/);
    if (match) return `删除通知渠道: ${match[1]}`;
  }
  if (action === 'TEST_NOTIFY_CONFIG') {
    const match = details.match(/Sent Test Notification to Channel (.+)/);
    if (match) return `向通知渠道 ${match[1]} 发送测试消息`;
  }
  
  if (action === 'SET_SYSTEM_CONFIG') {
    const match = details.match(/Configured System key:\s*(.+)/);
    if (match) return `配置系统参数项: ${match[1]}`;
  }

  if (action === 'IDEMPOTENCY_CONFLICT') {
    return `操作由于幂等键冲突被拦截 (${details})`;
  }
  
  return details;
}
</script>

<style scoped>
.tab-alert {
  margin: 14px 16px;
}

.skeleton-block {
  padding: 20px;
}

.channel-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  padding: 16px;
}

.channel-card {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius);
  background: #fafafa;
}

.channel-card__top,
.channel-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.channel-meta {
  display: grid;
  gap: 6px;
  color: var(--text-muted);
}

.channel-meta code {
  display: block;
  padding: 8px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #fff;
  color: var(--text);
}

.event-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.audit-toolbar {
  display: flex;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
  flex-wrap: wrap;
}

.audit-table {
  min-width: 860px;
}

.pagination-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 14px 16px;
  border-top: 1px solid var(--line-soft);
  background: #fafafa;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
