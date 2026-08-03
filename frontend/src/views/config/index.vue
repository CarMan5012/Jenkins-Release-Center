<template>
  <section class="page config-page">
    <div class="page-header">
      <div>
        <h1>系统配置</h1>
        <p>通知渠道、安全审计、自动任务与高级配置。</p>
      </div>
    </div>

    <n-tabs type="segment" animated>
      <n-tab-pane name="notify" tab="通知渠道">
        <section class="panel">
          <div class="panel__header">
            <h2 class="panel__title">通知渠道</h2>
            <UiverseButton variant="primary" size="sm" @click="openNotifyModal()">添加渠道</UiverseButton>
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
                  <td>{{ translateDetails(log.action, log.details || undefined) }}</td>
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

      <n-tab-pane name="tasks" tab="自动任务">
        <!-- 后台定时任务与数据存储说明卡片 -->
        <section class="panel" style="margin-bottom: 20px;">
          <div class="panel__header" style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <h2 class="panel__title">后台自动任务与运行状态</h2>
            </div>
            <RefreshButton secondary size="small" label="刷新调度状态" :loading="schedulerLoading" @click="loadSchedulerInfo(true)" />
          </div>

          <div v-if="schedulerLoading && !schedulerInfo" style="padding: 24px;">
            <n-skeleton text :repeat="6" />
          </div>
          <div v-else-if="schedulerInfo" style="padding: 20px 24px; display: grid; gap: 20px;">
            
            <!-- 引擎概况与存储状态卡片 -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px;">
              <div class="info-metric-card">
                <div class="muted text-xs">后台调度服务</div>
                <div style="margin-top: 6px; display: flex; align-items: center; gap: 8px;">
                  <n-tag
                    size="small"
                    :type="schedulerInfo.scheduler_summary?.status === 'RUNNING' ? 'success' : 'error'"
                    :bordered="false"
                  >
                    {{ schedulerInfo.scheduler_summary?.status === 'RUNNING'
                      ? '● 正常运行'
                      : '● 已停止' }}
                  </n-tag>
                </div>
                <div class="muted text-xs" style="margin-top: 4px;">已接管全量后台自动任务</div>
              </div>

              <div class="info-metric-card">
                <div class="muted text-xs">系统数据库存储</div>
                <div style="margin-top: 6px; font-size: 18px; font-weight: 700;" class="mono primary-color">
                  {{ schedulerInfo.storage_summary?.db_size }}
                </div>
                <div class="muted text-xs" style="margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" :title="schedulerInfo.storage_summary?.db_file_path">
                  路径: {{ schedulerInfo.storage_summary?.db_file_path }}
                </div>
              </div>

              <div class="info-metric-card">
                <div class="muted text-xs">Jenkins 配置备份</div>
                <div style="margin-top: 6px; font-size: 16px; font-weight: 700;" class="mono">
                  {{ schedulerInfo.storage_summary?.server_count || 0 }} 实例 / {{ schedulerInfo.storage_summary?.backup_count || 0 }} 份备份
                </div>
                <div class="muted text-xs" style="margin-top: 4px;">
                  占用空间: <strong class="mono">{{ schedulerInfo.storage_summary?.backup_total_size || '0 B' }}</strong> (最新: {{ schedulerInfo.storage_summary?.latest_backup_at || '暂无备份点' }})
                </div>
              </div>
            </div>

            <!-- 常驻系统定时任务表格 -->
            <div>
              <h4 style="font-size: 13.5px; font-weight: 600; margin: 0 0 10px 0;">系统常驻后台自动任务</h4>
              <div class="table-wrap">
                <table class="ops-table">
                  <thead>
                    <tr>
                      <th scope="col">任务名称与说明</th>
                      <th scope="col">触发频率与时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="task in schedulerInfo.system_cron_tasks" :key="task.job_id">
                      <td>
                        <div style="display: flex; align-items: center; gap: 6px;">
                          <span style="font-weight: 600;">{{ task.name }}</span>
                          <n-tooltip trigger="hover" placement="top">
                            <template #trigger>
                              <span class="task-help-badge">?</span>
                            </template>
                            {{ task.description }}
                          </n-tooltip>
                        </div>
                      </td>
                      <td class="mono text-xs">{{ task.trigger_desc }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- 动态发布排期任务列表 (若有) -->
            <div v-if="schedulerInfo.release_scheduled_jobs && schedulerInfo.release_scheduled_jobs.length">
              <h4 style="font-size: 13.5px; font-weight: 600; margin: 0 0 10px 0;">已预约的定时发布任务</h4>
              <div class="table-wrap">
                <table class="ops-table">
                  <thead>
                    <tr>
                      <th scope="col">关联发布计划</th>
                      <th scope="col">预计触发时间</th>
                      <th scope="col">错失宽限时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="job in schedulerInfo.release_scheduled_jobs" :key="job.job_id">
                      <td><strong>{{ job.plan_name }}</strong> <span class="muted mono text-xs">(Plan #{{ job.plan_id }})</span></td>
                      <td class="mono text-xs">{{ job.next_run_time }}</td>
                      <td class="mono text-xs">{{ job.misfire_grace_time }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        </section>
      </n-tab-pane>

      <n-tab-pane name="advanced" tab="高级配置">

        <section class="panel">
          <div class="panel__header">
            <h2 class="panel__title">高级策略与基础设置</h2>
          </div>
          <div style="padding: 20px 24px; max-width: 500px;">
            <n-form label-placement="left" label-width="170" size="small" :show-feedback="false" style="display: grid; gap: 16px;">
              <n-form-item>
                <template #label>
                  <span style="white-space: nowrap; display: inline-flex; align-items: center;">
                    <span>系统外部访问地址</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <span class="help-icon">?</span>
                      </template>
                      <span>用于发送钉钉通知等外部跳转时的链接前缀。例如: http://jenkins.com</span>
                    </n-tooltip>
                  </span>
                </template>
                <n-input v-model:value="systemUrl" placeholder="默认: http://localhost:3000" />
              </n-form-item>
              <n-form-item>
                <template #label>
                  <span style="white-space: nowrap; display: inline-flex; align-items: center;">
                    <span>安全审计日志保留</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <span class="help-icon">?</span>
                      </template>
                      <span>设定安全审计日志的最大保留天数。设为 0 或留空则永久保留。</span>
                    </n-tooltip>
                  </span>
                </template>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <div class="el-number-input">
                    <input
                      type="number"
                      v-model.number="auditRetention"
                      min="0"
                      class="el-number-input__inner"
                      placeholder="默认 30"
                    />
                    <div class="el-number-input__controls">
                      <button
                        type="button"
                        class="el-number-input__increase"
                        title="增加 1 天"
                        @click="auditRetention = (auditRetention || 0) + 1"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 320l320 384H192z"></path></svg>
                      </button>
                      <button
                        type="button"
                        class="el-number-input__decrease"
                        title="减少 1 天"
                        @click="auditRetention = Math.max(0, (auditRetention || 0) - 1)"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 704L192 320h640z"></path></svg>
                      </button>
                    </div>
                  </div>
                  <span style="font-size: 12px; color: #6b7280;">天</span>
                </div>
              </n-form-item>
              <n-form-item>
                <template #label>
                  <span style="white-space: nowrap; display: inline-flex; align-items: center;">
                    <span>执行历史与日志保留</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <span class="help-icon">?</span>
                      </template>
                      <span>设定构建发布历史及日志缓存的最大保留天数。设为 0 或留空则永久保留。</span>
                    </n-tooltip>
                  </span>
                </template>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <div class="el-number-input">
                    <input
                      type="number"
                      v-model.number="historyRetention"
                      min="0"
                      class="el-number-input__inner"
                      placeholder="默认 30"
                    />
                    <div class="el-number-input__controls">
                      <button
                        type="button"
                        class="el-number-input__increase"
                        title="增加 1 天"
                        @click="historyRetention = (historyRetention || 0) + 1"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 320l320 384H192z"></path></svg>
                      </button>
                      <button
                        type="button"
                        class="el-number-input__decrease"
                        title="减少 1 天"
                        @click="historyRetention = Math.max(0, (historyRetention || 0) - 1)"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 704L192 320h640z"></path></svg>
                      </button>
                    </div>
                  </div>
                  <span style="font-size: 12px; color: #6b7280;">天</span>
                </div>
              </n-form-item>
              <n-form-item>
                <template #label>
                  <span style="white-space: nowrap; display: inline-flex; align-items: center;">
                    <span>计划列表保留</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <span class="help-icon">?</span>
                      </template>
                      <span>只清理已结束计划；等待中和运行中计划不会删除。设为 0 则永久保留。</span>
                    </n-tooltip>
                  </span>
                </template>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <div class="el-number-input">
                    <!-- v-model:value="planRetention" -->
                    <input
                      type="number"
                      v-model.number="planRetention"
                      min="0"
                      class="el-number-input__inner"
                      placeholder="默认 30"
                    />
                    <div class="el-number-input__controls">
                      <button
                        type="button"
                        class="el-number-input__increase"
                        title="增加 1 天"
                        @click="planRetention = (planRetention || 0) + 1"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 320l320 384H192z"></path></svg>
                      </button>
                      <button
                        type="button"
                        class="el-number-input__decrease"
                        title="减少 1 天"
                        @click="planRetention = Math.max(0, (planRetention || 0) - 1)"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 704L192 320h640z"></path></svg>
                      </button>
                    </div>
                  </div>
                  <span style="font-size: 12px; color: #6b7280;">天</span>
                </div>
              </n-form-item>
              <n-form-item>
                <template #label>
                  <span style="white-space: nowrap; display: inline-flex; align-items: center;">
                    <span>Jenkins 备份保留数量</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <span class="help-icon">?</span>
                      </template>
                      <span>设定每个 Jenkins 实例最大保留的备份记录数量。设为 0 或留空则永久保留。</span>
                    </n-tooltip>
                  </span>
                </template>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <div class="el-number-input">
                    <input
                      type="number"
                      v-model.number="backupRetention"
                      min="0"
                      class="el-number-input__inner"
                      placeholder="默认 10"
                    />
                    <div class="el-number-input__controls">
                      <button
                        type="button"
                        class="el-number-input__increase"
                        title="增加 1 个"
                        @click="backupRetention = (backupRetention || 0) + 1"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 320l320 384H192z"></path></svg>
                      </button>
                      <button
                        type="button"
                        class="el-number-input__decrease"
                        title="减少 1 个"
                        @click="backupRetention = Math.max(0, (backupRetention || 0) - 1)"
                      >
                        <svg viewBox="0 0 1024 1024" width="6" height="6" fill="currentColor"><path d="M512 704L192 320h640z"></path></svg>
                      </button>
                    </div>
                  </div>
                  <span style="font-size: 12px; color: #6b7280;">个</span>
                </div>
              </n-form-item>
              <div style="margin-top: 10px; display: flex; justify-content: flex-start;">
                <UiverseButton variant="primary" size="sm" :loading="submitAdvancedLoading" @click="submitAdvancedSettings">
                  保存设置
                </UiverseButton>
              </div>
            </n-form>

            <n-divider style="margin: 32px 0 24px 0;" />

            <div class="reset-sequence-block">
              <div style="display: flex; align-items: center; gap: 4px; margin-bottom: 12px;">
                <h3 style="font-size: 15px; font-weight: 600; margin: 0;">历史记录 ID 序号重置</h3>
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <span class="help-icon">?</span>
                  </template>
                  <span>清空当前的发布历史与外部构建记录，将数据库自增主键序号彻底归零重置。重置后，系统产生的新构建记录将重新从 ID #1 开始计算。</span>
                </n-tooltip>
              </div>
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
        <n-form-item label="Secret">
          <n-input
            v-model:value="notifyForm.secret"
            type="password"
            show-password-on="click"
            :placeholder="editingNotifyId ? '留空保持不变' : '请输入 Secret'"
          />
        </n-form-item>
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
  NModal,
  NPagination,
  NSelect,
  NSkeleton,
  NSpace,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui';
import type { FormInst, FormRules } from 'naive-ui';
import RefreshButton from '../../components/RefreshButton.vue';
import StatusBadge from '../../components/StatusBadge.vue';
import UiverseButton from '../../components/uiverse/UiverseButton.vue';
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
const schedulerLoading = ref(false);
const schedulerInfo = ref<any>(null);

// 本地 SWR 快照存储
let schedulerCacheRaw: any = null;

async function loadSchedulerInfo(force = false) {
  if (schedulerCacheRaw && !schedulerInfo.value && !force) {
    schedulerInfo.value = schedulerCacheRaw;
  } else if (!schedulerInfo.value) {
    schedulerLoading.value = true;
  }
  try {
    const res = await request.get('/system/scheduler-info', {
      params: force ? { force_refresh: true } : {}
    });
    schedulerInfo.value = res.data;
    schedulerCacheRaw = res.data;
  } catch (e: any) {
    message.error(e.message || '加载调度任务状态失败');
  } finally {
    schedulerLoading.value = false;
  }
}

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
    notifyError.value = err.message || '通知配置加载失败';
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
    if (resetPage) {
      message.success('查询成功');
    }
  } catch (err: any) {
    auditError.value = err.message || '审计日志加载失败';
    message.error(err.message || '查询失败');
  } finally {
    if (resetPage) {
      const remaining = 500 - (Date.now() - startedAt);
      if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
    }
    auditLoading.value = false;
  }
}

function translateAction(action: string) {
  if (!action) return '未知操作';
  const actionMap: Record<string, string> = {
    'LOGIN': '系统登录',
    'LOGOUT': '退出登录',
    'CREATE_RELEASE_PLAN': '创建发布计划',
    'UPDATE_RELEASE_PLAN': '更新发布计划',
    'DELETE_RELEASE_PLAN': '删除发布计划',
    'CANCEL_RELEASE_PLAN': '取消发布计划',
    'TRIGGER_RELEASE_PLAN': '手动触发发布',
    'RETRY_RELEASE_PLAN': '重试整单发布',
    'RETRY_SINGLE_TASK': '重试单项构建',
    'IDEMPOTENCY_CONFLICT': '重复请求拦截',

    'CREATE_JENKINS_SERVER': '添加 Jenkins 实例',
    'UPDATE_JENKINS_SERVER': '更新 Jenkins 实例',
    'DELETE_JENKINS_SERVER': '删除 Jenkins 实例',
    'SYNC_JENKINS': '手动同步 Jenkins 任务',
    'RUN_JENKINS_JOB_DIRECTLY': '直接触发 Job 构建',

    'CREATE_BACKUP': '创建配置备份',
    'VIEW_BACKUP_DETAILS': '查看备份详情',
    'DOWNLOAD_BACKUP': '下载加密备份',
    'DELETE_BACKUP': '删除配置备份',

    'CREATE_NOTIFY_CONFIG': '新增通知渠道',
    'UPDATE_NOTIFY_CONFIG': '更新通知渠道',
    'DELETE_NOTIFY_CONFIG': '删除通知渠道',
    'TEST_NOTIFY_CONFIG': '测试通知渠道',

    'SET_SYSTEM_CONFIG': '变更高级配置项',
    'RESET_SEQUENCE': '重置历史记录 ID',
  };

  return actionMap[action.toUpperCase()] || action;
}

function translateDetails(_action: string, details?: string) {
  if (!details) return '-';
  
  let result = details;

  result = result
    .replace(/^User\s+(.*?)\s+successfully logged in\.?/i, '用户 $1 成功登录系统')
    .replace(/^User\s+(.*?)\s+successfully logged out\.?/i, '用户 $1 成功退出系统')
    .replace(/^Created Notification Channel\s*/i, '创建通知渠道: ')
    .replace(/^Updated Notification Channel\s*/i, '更新通知渠道: ')
    .replace(/^Deleted Notification Channel\s*/i, '删除通知渠道: ')
    .replace(/^Sent Test Notification to Channel\s*/i, '测试发送通知渠道: ')
    .replace(/^Configured System key:\s*/i, '修改系统配置项: ')
    .replace(/^Idempotency conflict for plan creation key\s*/i, '重复触发发布计划: ')
    .replace(/^Created release plan:\s*/i, '创建发布计划: ')
    .replace(/^Cancelled release plan:\s*/i, '取消发布计划: ')
    .replace(/^Triggered release plan early:\s*/i, '提前触发发布计划: ')
    .replace(/^Retried release plan in-place:\s*/i, '重新发起发布计划: ')
    .replace(/^Retried single task\s*/i, '重试单项构建: ')
    .replace(/^Deleted release plan ID:\s*/i, '删除发布计划 ID: ')
    .replace(/^Updated release plan:\s*/i, '更新发布计划: ')
    .replace(/^Created Jenkins Server\s*/i, '添加 Jenkins 实例: ')
    .replace(/^Updated Jenkins Server\s*/i, '更新 Jenkins 实例: ')
    .replace(/^Deleted Jenkins Server\s*/i, '删除 Jenkins 实例: ')
    .replace(/^and all its dependencies\s*/i, '及其全部依赖数据')
    .replace(/^Triggered background Views\/Jobs sync for server\s*/i, '手动触发任务同步: ')
    .replace(/^Directly triggered job\s*/i, '手动直接触发构建: ')
    .replace(/^Created backup task\s*/i, '创建配置备份: ')
    .replace(/^Viewed backup details for backup\s*/i, '查看备份详情: ')
    .replace(/^Downloaded encrypted backup\s*/i, '下载加密备份包: ')
    .replace(/^Deleted backup\s*/i, '删除配置备份: ')
    .replace(/for server/gi, '（实例：')
    .replace(/in plan/gi, '，所属计划：');

  return result;
}

function openNotifyModal(item?: NotifyConfig) {
  editingNotifyId.value = item?.id || null;
  notifyForm.value = item ? {
    name: item.name,
    channel_type: item.channel_type,
    webhook_url: item.webhook_url,
    secret: '',
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
      if (payload.secret && payload.secret.trim() !== '') {
        payload.secret = await encryptData(payload.secret.trim());
      } else if (editingNotifyId.value) {
        delete (payload as any).secret;
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
    content: `确认删除 ${item.name}`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      busyKey.value = `delete-${item.id}`;
      try {
        await request.delete(`/system/notify-configs/${item.id}`);
        message.success('已删除');
        await loadNotifyConfigs();
      } catch (err: any) {
        message.error(err.message || '删除失败');
      } finally {
        busyKey.value = '';
      }
    },
  });
}

const auditRetention = ref<number | null>(30);
const historyRetention = ref<number | null>(30);
const planRetention = ref<number | null>(30);
const backupRetention = ref<number | null>(10);
const systemUrl = ref<string>('');
const submitAdvancedLoading = ref(false);

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

    const backupCount = configs.find((c: any) => c.config_key === 'jenkins_backup_retention_count');
    backupRetention.value = backupCount ? parseInt(backupCount.config_value) : 10;
    
    const sysUrl = configs.find((c: any) => c.config_key === 'system_url');
    systemUrl.value = sysUrl ? sysUrl.config_value : '';
    await loadSchedulerInfo();
  } catch (err: any) {
    message.error(err.message || '加载系统配置失败');
  }
}

async function submitAdvancedSettings() {
  submitAdvancedLoading.value = true;
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
      }),
      request.post('/system/configs', {
        config_key: 'jenkins_backup_retention_count',
        config_value: String(backupRetention.value ?? 10),
        description: 'Jenkins 备份最大保留数量 (个，0表示永久保留)'
      }),
      request.post('/system/configs', {
        config_key: 'system_url',
        config_value: systemUrl.value.trim(),
        description: '系统外部访问地址前缀 (供第三方通知渠道如钉钉回调使用)'
      })
    ]);
    message.success('高级设置保存成功');
  } catch (err: any) {
    message.error(err.message || '高级设置保存失败');
  } finally {
    submitAdvancedLoading.value = false;
  }
}

const resetSeqLoading = ref(false);

function confirmResetSequence() {
  dialog.warning({
    title: '确认重置历史记录 ID 序号',
    content: '此操作将清空当前的发布历史与外部构建记录，并重置数据库计数器，重置后系统产生的新构建记录将重新从 ID #1 开始计算',
    positiveText: '确认重置归零',
    negativeText: '取消',
    onPositiveClick: async () => {
      resetSeqLoading.value = true;
      try {
        const res = await request.post('/history/reset-sequence');
        message.success(res.data?.message || '历史记录 ID 已重置，从 #1 重新开始计算');
      } catch (err: any) {
        message.error(err.message || '重置历史记录 ID 失败');
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
</script>

<style scoped>
.help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  margin-left: 3px;
  position: relative;
  top: -5px;
  border-radius: 50%;
  background: #e5e7eb;
  color: #6b7280;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
  user-select: none;
  transition: all 0.2s ease;
}

.help-icon:hover {
  background: #1890ff;
  color: #ffffff;
  transform: scale(1.15);
}

/* 纯正 Element Plus 风格数字微调输入框控件 (紧凑小巧版) */
.el-number-input {
  display: inline-flex;
  align-items: center;
  position: relative;
  width: 64px;
  height: 24px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background-color: #ffffff;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  overflow: hidden;
  box-sizing: border-box;
}

.el-number-input:hover {
  border-color: #c0c4cc;
}

.el-number-input:focus-within {
  border-color: #409eff;
  box-shadow: 0 0 0 1px #409eff;
}

.el-number-input__inner {
  width: calc(100% - 18px);
  height: 100%;
  padding: 0 4px 0 6px;
  border: none;
  outline: none;
  background: transparent;
  font-size: 12px;
  color: #606266;
  box-sizing: border-box;
}

.el-number-input__inner::-webkit-outer-spin-button,
.el-number-input__inner::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.el-number-input__inner[type=number] {
  -moz-appearance: textfield;
}

.el-number-input__controls {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 18px;
  display: flex;
  flex-direction: column;
  border-left: 1px solid #dcdfe6;
  background-color: #f5f7fa;
  box-sizing: border-box;
}

.el-number-input__increase,
.el-number-input__decrease {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 50%;
  border: none;
  background: transparent;
  padding: 0;
  margin: 0;
  cursor: pointer;
  color: #909399;
  transition: color 0.15s ease, background-color 0.15s ease;
  box-sizing: border-box;
}

.el-number-input__increase {
  border-bottom: 1px solid #e4e7ed;
}

.el-number-input__increase:hover,
.el-number-input__decrease:hover {
  color: #409eff;
  background-color: #ecf5ff;
}

.el-number-input__increase:active,
.el-number-input__decrease:active {
  color: #337ecc;
}

.el-number-input__increase svg,
.el-number-input__decrease svg {
  display: block;
  margin: 0 auto;
  fill: currentColor;
}

:deep(.n-popover:not(.n-popover--raw)) {
  background-color: #ffffff !important;
  color: #374151 !important;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08), 0 1px 4px rgba(0, 0, 0, 0.04) !important;
  border: 1px solid #e5e7eb !important;
}

:deep(.n-popover-arrow) {
  background-color: #ffffff !important;
}

.tab-alert {
  margin: 14px 16px;
}

.task-help-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted, #9ca3af);
  border: 1px solid var(--line-soft, #d1d5db);
  border-radius: 50%;
  cursor: help;
  transition: all 0.2s ease;
  user-select: none;
}

.task-help-badge:hover {
  color: var(--primary-color, #2563eb);
  border-color: var(--primary-color, #2563eb);
  background: rgba(37, 99, 235, 0.08);
}

.skeleton-block {
  padding: 20px;
}

.info-metric-card {
  padding: 14px 16px;
  background: var(--bg-card, #ffffff);
  border: 1px solid var(--line-soft, rgba(60, 60, 67, 0.12));
  border-radius: 10px;
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

.channel-card__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.channel-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
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

<style>
/* 强制覆盖 Teleport 至 document.body 的 Naive UI Tooltip 为纯白精致卡片主题 */
body .n-popover.n-popover--tooltip,
body .n-popover-shared.n-popover--tooltip {
  background-color: #ffffff !important;
  color: #1f2937 !important;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1), 0 1px 4px rgba(0, 0, 0, 0.04) !important;
  border: 1px solid #e5e7eb !important;
  font-size: 12px !important;
  border-radius: 6px !important;
}

body .n-popover.n-popover--tooltip .n-popover-arrow,
body .n-popover-shared.n-popover--tooltip .n-popover-arrow {
  background-color: #ffffff !important;
}
</style>
