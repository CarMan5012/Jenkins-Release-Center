<template>
  <n-drawer v-model:show="visible" :width="720" placement="right" @after-leave="onClosed">
    <n-drawer-content closable>
      <template #header>
        <div class="backup-drawer-header">
          <span>备份管理 · {{ server?.name }}</span>
          <n-button type="primary" size="small" :loading="backingUp" @click="triggerNewBackup">
            新建配置备份
          </n-button>
        </div>
      </template>

      <n-alert v-if="error" type="error" :bordered="false" class="backup-alert">
        {{ error }}
      </n-alert>

      <div v-if="loading" class="backup-loading">
        <n-skeleton text :repeat="8" />
      </div>

      <div v-else class="table-wrap">
        <table class="ops-table backup-table">
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">状态</th>
              <th scope="col">Job 数量</th>
              <th scope="col">备份时间</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in backups" :key="item.id">
              <td class="mono">#{{ item.id }}</td>
              <td>
                <n-tag :type="statusType(item.status)" :bordered="false" size="small">
                  <template v-if="item.status === 'BACKUPING'" #icon>
                    <n-spin :size="12" />
                  </template>
                  {{ statusText(item.status) }}
                </n-tag>
              </td>
              <td class="mono">{{ item.job_count }} 个</td>
              <td class="mono">{{ formatDateTime(item.backup_time) }}</td>
              <td>
                <div class="cell-actions">
                  <n-button
                    size="tiny"
                    secondary
                    :disabled="item.status !== 'SUCCESS'"
                    @click="showSummary(item.id)"
                  >
                    查看汇总
                  </n-button>
                  <n-button
                    size="tiny"
                    secondary
                    :disabled="item.status !== 'SUCCESS'"
                    @click="downloadZip(item.id)"
                  >
                    下载配置
                  </n-button>
                  <n-popconfirm @positive-click="deleteBackup(item.id)">
                    <template #trigger>
                      <n-button
                        size="tiny"
                        type="error"
                        secondary
                        :disabled="item.status === 'BACKUPING'"
                      >
                        删除
                      </n-button>
                    </template>
                    确定彻底删除此备份吗？相关文件也将从磁盘清除。
                  </n-popconfirm>
                </div>
              </td>
            </tr>
            <tr v-if="!backups.length">
              <td colspan="5"><div class="empty-inline">暂无备份记录。</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </n-drawer-content>
  </n-drawer>

  <n-modal
    v-model:show="summaryVisible"
    preset="card"
    title="备份汇总报告"
    class="backup-summary-modal"
    style="width: min(1500px, 96vw)"
    @after-leave="clearSummary"
  >
    <div v-if="summaryLoading" class="backup-loading">
      <n-skeleton text :repeat="8" />
    </div>

    <template v-else-if="details.available">
      <div class="backup-summary-meta">
        <span>共 {{ details.jobs.length }} 个 Job</span>
        <span>悬浮查看详情，点击按钮复制</span>
      </div>

      <nav v-if="details.views.length" class="backup-view-tabs" aria-label="Jenkins 视图">
        <button
          v-for="view in details.views"
          :key="view.name"
          type="button"

          class="backup-view-tab"
          :aria-pressed="activeViewName === view.name"
          :class="{ 'backup-view-tab--active': activeViewName === view.name }"
          @click="activeViewName = view.name"
        >
          <span>{{ view.name }}</span>
          <small>{{ view.job_names.length }}</small>
        </button>
      </nav>

      <div class="backup-summary-scroll">
        <table class="backup-summary-table">
          <thead>
            <tr>
              <th>Job 名称</th>
              <th>类型</th>
              <th>状态</th>
              <th>Git 仓库 / SCM</th>
              <th>分支</th>
              <th>环境 / 参数</th>
              <th>构建触发器</th>
              <th>构建脚本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in visibleJobs" :key="job.name">
              <td class="backup-job-name"><code>{{ job.name }}</code></td>
              <td>{{ job.project_type || '-' }}</td>
              <td>
                <n-tag :type="job.status === 'disabled' ? 'error' : 'success'" size="small" :bordered="false">
                  {{ job.status === 'disabled' ? '禁用' : '启用' }}
                </n-tag>
              </td>
              <td>
                <div v-if="job.git_urls.length" class="backup-cell-list">
                  <code v-for="url in job.git_urls" :key="url">{{ url }}</code>
                </div>
                <span v-else class="backup-muted">无</span>
              </td>
              <td>
                <div v-if="job.branches.length" class="backup-cell-list">
                  <code v-for="branch in job.branches" :key="branch">{{ branch }}</code>
                </div>
                <span v-else class="backup-muted">无</span>
              </td>
              <td>
                <div v-if="hasEnvironmentDetails(job)" class="backup-category-list">
                  <n-popover v-if="job.environment.variables.length" trigger="hover" placement="bottom" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--env">
                        <span>环境变量</span>
                        <b>{{ job.environment.variables.length }}</b>
                      </button>
                    </template>
                    <div class="backup-detail-popover backup-detail-popover--compact">
                      <h4>环境变量配置</h4>
                      <div v-for="variable in job.environment.variables" :key="variable.name" class="backup-detail-row">
                        <code>{{ environmentCopyText(variable) }}</code>
                        <n-button size="tiny" text type="primary" @click="copyText(environmentCopyText(variable), variable.name)">
                          复制
                        </n-button>
                      </div>
                    </div>
                  </n-popover>

                  <n-popover v-if="job.parameters.length" trigger="hover" placement="bottom" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--params">
                        <span>构建参数</span>
                        <b>{{ job.parameters.length }}</b>
                      </button>
                    </template>
                    <div class="backup-detail-popover backup-detail-popover--compact">
                      <h4>构建参数配置</h4>
                      <div v-for="parameter in job.parameters" :key="parameter.name" class="backup-detail-row">
                        <div>
                          <strong>{{ parameter.name }}</strong>
                          <small>{{ parameterTypeLabel(parameter.type) }} · 默认值：{{ parameter.default_value || '空' }}</small>
                        </div>
                        <n-button size="tiny" text type="primary" @click="copyText(parameter.name + '=' + (parameter.default_value || ''), parameter.name)">
                          复制
                        </n-button>
                      </div>
                    </div>
                  </n-popover>

                  <n-popover v-if="job.credentials.length" trigger="hover" placement="bottom" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--credentials">
                        <span>凭据变量</span>
                        <b>{{ job.credentials.length }}</b>
                      </button>
                    </template>
                    <div class="backup-detail-popover backup-detail-popover--compact">
                      <h4>凭据变量配置（当前为明文）</h4>
                      <article v-for="credential in job.credentials" :key="credential.id" class="backup-credential">
                        <div class="backup-credential-title">
                          <strong>{{ credential.id }}</strong>
                          <n-tag size="tiny" :bordered="false">{{ credentialTypeLabel(credential.type) }}</n-tag>
                          <n-button
                            v-if="!credential.value_unavailable"
                            size="tiny"
                            text
                            type="primary"
                            @click="copyText(credentialCopyText(credential), credential.id)"
                          >
                            复制全部
                          </n-button>
                        </div>
                        <span v-if="credential.value_unavailable" class="backup-muted">Jenkins 未授权读取或类型不支持</span>
                        <div v-for="row in credentialRows(credential)" v-else :key="row.name" class="backup-detail-row">
                          <code>{{ row.text }}</code>
                          <n-button size="tiny" text type="primary" @click="copyText(row.text, row.name)">
                            复制
                          </n-button>
                        </div>
                      </article>
                    </div>
                  </n-popover>

                  <n-popover v-if="Object.keys(job.environment.tools).length" trigger="hover" placement="bottom" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--tools">
                        <span>构建环境</span>
                        <b>{{ Object.keys(job.environment.tools).length }}</b>
                      </button>
                    </template>
                    <div class="backup-detail-popover backup-detail-popover--compact">
                      <h4>构建环境配置</h4>
                      <div v-for="tool in toolRows(job)" :key="tool.name" class="backup-detail-row">
                        <code>{{ toolNameLabel(tool.name) }}：{{ tool.value }}</code>
                        <n-button size="tiny" text type="primary" @click="copyText(tool.name + '=' + tool.value, toolNameLabel(tool.name))">
                          复制
                        </n-button>
                      </div>
                    </div>
                  </n-popover>
                </div>
                <span v-else class="backup-muted">无配置</span>
              </td>
              <td>
                <div v-if="job.triggers.length" class="backup-cell-list">
                  <span v-for="trigger in triggerLabels(job)" :key="trigger">{{ trigger }}</span>
                </div>
                <span v-else class="backup-muted">手动触发</span>
              </td>
              <td>
                <div class="backup-category-list">
                  <n-popover v-if="job.maven_config" trigger="hover" placement="bottom" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--maven">
                        <span>Maven 构建</span>
                        <b>1</b>
                      </button>
                    </template>
                    <div class="backup-detail-popover backup-detail-popover--compact">
                      <h4>Maven 构建配置</h4>
                      <div class="backup-detail-row">
                        <div>
                          <strong>项目配置文件</strong>
                          <small>{{ job.maven_config.root_pom || 'pom.xml' }}</small>
                        </div>
                        <n-button size="tiny" text type="primary" @click="copyText(job.maven_config.root_pom || 'pom.xml', '项目配置文件')">
                          复制
                        </n-button>
                      </div>
                      <div class="backup-detail-row">
                        <div>
                          <strong>构建目标与参数</strong>
                          <small>{{ job.maven_config.goals || '未配置' }}</small>
                        </div>
                        <n-button v-if="job.maven_config.goals" size="tiny" text type="primary" @click="copyText(job.maven_config.goals, '构建目标与参数')">
                          复制
                        </n-button>
                      </div>
                    </div>
                  </n-popover>

                  <n-popover v-if="job.scripts.length" trigger="hover" placement="bottom-end" :show-arrow="false">
                    <template #trigger>
                      <button type="button" class="backup-category-chip backup-category-chip--scripts">
                        <span>构建步骤</span>
                        <b>{{ job.scripts.length }}</b>
                      </button>
                    </template>

                    <div class="backup-script-popover">
                      <div class="backup-script-toolbar">
                        <div class="backup-script-tabs">
                          <button
                            v-for="(script, index) in job.scripts"
                            :key="script.filename"
                            type="button"
                            class="backup-script-tab"
                            :class="{ 'backup-script-tab--active': activeScriptIndex(job) === index }"
                            @click="setActiveScript(job, index)"
                          >
                            <strong>{{ scriptDisplayName(script, index) }}</strong>
                            <small>{{ script.filename }}</small>
                          </button>
                        </div>
                        <n-button size="tiny" type="primary" @click="copyActiveScript(job)">
                          复制当前脚本
                        </n-button>
                      </div>
                      <pre><code>{{ activeScript(job)?.content }}</code></pre>
                    </div>
                  </n-popover>
                  <span v-if="!job.maven_config && !job.scripts.length" class="backup-muted">无构建脚本</span>
                </div>
              </td>
            </tr>
            <tr v-if="!visibleJobs.length">
              <td colspan="8"><div class="empty-inline">当前视图没有 Job。</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div v-else class="markdown-preview" v-html="legacyHtml"></div>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NModal,
  NPopconfirm,
  NPopover,
  NSkeleton,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui';
import request from '../../utils/request';
import { wsService } from '../../utils/websocket';
import { formatDateTime } from '../../utils/release-ui';
import {
  credentialCopyText,
  environmentCopyText,
  normalizeBackupDetails,
  scriptDisplayName,
  type BackupCredential,
  type BackupDetails,
  type BackupJobDetail,
  type BackupScript,
} from '../../utils/jenkins-backup';

interface BackupItem {
  id: number;
  status: string;
  job_count: number;
  backup_time: string;
}

const props = defineProps<{
  show: boolean;
  server: { id: number; name: string } | null;
}>();

const emit = defineEmits<{ 'update:show': [value: boolean] }>();
const message = useMessage();
const visible = ref(false);
const loading = ref(false);
const backingUp = ref(false);
const backups = ref<BackupItem[]>([]);
const error = ref('');
const summaryVisible = ref(false);
const summaryLoading = ref(false);
const details = ref<BackupDetails>(normalizeBackupDetails(null));
const legacyHtml = ref('');
const activeScriptIndexes = ref<Record<string, number>>({});
const activeViewName = ref('');
let summaryRequestId = 0;
const visibleJobs = computed(() => {
  const view = details.value.views.find((item) => item.name === activeViewName.value);
  if (!view) return details.value.jobs;
  const jobNames = new Set(view.job_names);
  return details.value.jobs.filter((job) => jobNames.has(job.name));
});

let pollTimer: any = null;

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPollingIfNeeded() {
  const hasBackuping = backups.value.some((b) => b.status === 'BACKUPING');
  if (hasBackuping) {
    if (!pollTimer) {
      pollTimer = setInterval(() => {
        fetchBackups(true);
      }, 2000);
    }
  } else {
    stopPolling();
  }
}

const handleWsUpdate = () => fetchBackups(true);

watch(() => props.show, (newValue) => {
  visible.value = newValue;
  if (newValue && props.server) {
    fetchBackups();
    wsService.on('JENKINS_UPDATE', handleWsUpdate);
    wsService.on('BACKUP_UPDATE', handleWsUpdate);
  } else {
    stopPolling();
    wsService.off('JENKINS_UPDATE', handleWsUpdate);
    wsService.off('BACKUP_UPDATE', handleWsUpdate);
  }
});

watch(visible, (newValue) => emit('update:show', newValue));
watch(summaryVisible, (newValue) => {
  if (!newValue) summaryRequestId += 1;
}, { flush: 'sync' });

function onClosed() {
  stopPolling();
  wsService.off('JENKINS_UPDATE', handleWsUpdate);
  wsService.off('BACKUP_UPDATE', handleWsUpdate);
  backups.value = [];
  error.value = '';
}

function clearSummary() {
  details.value = normalizeBackupDetails(null);
  legacyHtml.value = '';
  activeScriptIndexes.value = {};
  activeViewName.value = '';
}

async function fetchBackups(silent = false) {
  if (!props.server) return;
  if (!silent) {
    loading.value = true;
  }
  error.value = '';
  try {
    const response = await request.get(`/jenkins/servers/${props.server.id}/backups`);
    backups.value = response.data || [];
    startPollingIfNeeded();
  } catch (err: any) {
    if (!silent) {
      error.value = err.message || '获取备份历史失败';
    }
  } finally {
    if (!silent) {
      loading.value = false;
    }
  }
}

async function triggerNewBackup() {
  if (!props.server) return;
  backingUp.value = true;
  try {
    await request.post(`/jenkins/servers/${props.server.id}/backups`);
    message.success('备份任务已提交后台执行');
    await fetchBackups(true);
  } catch (err: any) {
    message.error(err.message || '触发备份失败');
  } finally {
    backingUp.value = false;
  }
}

async function showSummary(backupId: number) {
  if (!props.server) return;
  const requestId = ++summaryRequestId;
  summaryVisible.value = true;
  summaryLoading.value = true;
  clearSummary();
  try {
    const response = await request.get(
      `/jenkins/servers/${props.server.id}/backups/${backupId}/details`,
    );
    if (requestId !== summaryRequestId) return;
    details.value = normalizeBackupDetails(response.data);
    const defaultView = details.value.views.find((view) => view.name.toLowerCase() === 'all')
      || details.value.views[0];
    activeViewName.value = defaultView?.name || '';
    if (!details.value.available) {
      const legacy = await request.get(
        `/jenkins/servers/${props.server.id}/backups/${backupId}/summary`,
      );
      if (requestId !== summaryRequestId) return;
      legacyHtml.value = renderMarkdownTable(legacy.data?.summary_md || '');
    }
  } catch (err: any) {
    if (requestId !== summaryRequestId) return;
    message.error(err.message || '获取报告失败，仅管理员可查看包含明文凭据的备份');
    summaryVisible.value = false;
  } finally {
    if (requestId === summaryRequestId) summaryLoading.value = false;
  }
}

async function downloadZip(backupId: number) {
  if (!props.server) return;
  try {
    const response = await request.get(
      `/jenkins/servers/${props.server.id}/backups/${backupId}/download`,
      { responseType: 'blob' },
    );
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `jenkins_backup_${backupId}.zip`;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (err: any) {
    message.error(err.message || '下载失败，仅管理员可下载包含明文凭据的备份');
  }
}

async function deleteBackup(backupId: number) {
  if (!props.server) return;
  try {
    await request.delete(`/jenkins/servers/${props.server.id}/backups/${backupId}`);
    message.success('备份已彻底删除');
    await fetchBackups();
  } catch (err: any) {
    message.error(err.message || '删除备份失败');
  }
}

function statusType(status: string): 'success' | 'error' | 'warning' {
  if (status === 'SUCCESS') return 'success';
  if (status === 'FAILED') return 'error';
  return 'warning';
}

function statusText(status: string) {
  if (status === 'SUCCESS') return '成功';
  if (status === 'FAILED') return '失败';
  return '备份中';
}

function hasEnvironmentDetails(job: BackupJobDetail) {
  return Boolean(
    job.environment.variables.length
    || job.parameters.length
    || job.credentials.length
    || Object.keys(job.environment.tools).length,
  );
}

function parameterTypeLabel(type: string) {
  const labels: Record<string, string> = {
    StringParameterDefinition: '文本参数',
    BooleanParameterDefinition: '开关参数',
    ChoiceParameterDefinition: '选项参数',
    PasswordParameterDefinition: '密码参数',
  };
  return labels[type] || '构建参数';
}

function credentialTypeLabel(type: string) {
  const labels: Record<string, string> = {
    StringBinding: '密文文本',
    secret_text: '密文文本',
    UsernamePasswordMultiBinding: '用户名和密码',
    username_password: '用户名和密码',
    FileBinding: '密文文件',
    ssh_private_key: 'SSH 私钥',
  };
  return labels[type] || 'Jenkins 凭据';
}

function toolNameLabel(name: string) {
  const labels: Record<string, string> = {
    nodejs: 'Node.js 运行环境',
    ansi_color: '彩色日志',
    timeout: '构建超时',
    clean_workspace_before_build: '构建前清理工作区',
  };
  return labels[name] || name;
}

function toolRows(job: BackupJobDetail) {
  return Object.entries(job.environment.tools).map(([name, value]) => ({
    name,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

function credentialRows(credential: BackupCredential) {
  return Object.entries(credential.bindings).map(([valueKey, variableName]) => {
    const value = credential.values[valueKey] || '';
    return {
      name: variableName,
      value,
      text: `${variableName}=${value}`,
    };
  });
}

function triggerLabels(job: BackupJobDetail) {
  return job.triggers.map((trigger) => String(trigger.desc || trigger.type || '未知触发器'));
}

function activeScriptIndex(job: BackupJobDetail) {
  return activeScriptIndexes.value[job.name] || 0;
}

function setActiveScript(job: BackupJobDetail, index: number) {
  activeScriptIndexes.value = { ...activeScriptIndexes.value, [job.name]: index };
}

function activeScript(job: BackupJobDetail): BackupScript | undefined {
  return job.scripts[activeScriptIndex(job)] || job.scripts[0];
}

function copyActiveScript(job: BackupJobDetail) {
  const script = activeScript(job);
  if (script) copyText(script.content, script.filename);
}

async function copyText(text: string, label: string) {
  try {
    if (!navigator.clipboard) throw new Error('Clipboard API unavailable');
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) {
      message.error('复制失败，请手动选择内容。');
      return;
    }
  }
  message.success('已复制 ' + label);
}

function renderMarkdownTable(markdown: string) {
  if (!markdown) return '';

  const escapeHtml = (str: string): string => {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  };

  const lines = markdown.split('\n');
  let inTable = false;
  let html = '';

  for (let line of lines) {
    line = line.trim();
    if (line.startsWith('|')) {
      if (!inTable) {
        inTable = true;
        html += '<table class="backup-summary-table"><thead>';
      }
      const rawColumns = line.split('|').slice(1, -1);
      if (rawColumns.every((column) => column.trim().startsWith('-'))) {
        html += '</thead><tbody>';
        continue;
      }
      html += '<tr>';
      for (const rawColumn of rawColumns) {
        const safeColumn = escapeHtml(rawColumn.trim());
        const content = safeColumn
          .replace(/`([^`]+)`/g, '<code>$1</code>')
          .replace(/&lt;br&gt;/g, '<br/>')
          .replace(/<br>/g, '<br/>')
          .replace(/&lt;div([^&gt;]*)&gt;/g, '<div$1>')
          .replace(/&lt;\/div&gt;/g, '</div>')
          .replace(/&lt;span([^&gt;]*)&gt;/g, '<span$1>')
          .replace(/&lt;\/span&gt;/g, '</span>')
          .replace(/&lt;code([^&gt;]*)&gt;/g, '<code$1>')
          .replace(/&lt;\/code&gt;/g, '</code>');
        html += html.includes('<tbody>') ? `<td>${content}</td>` : `<th>${content}</th>`;
      }
      html += '</tr>';
    } else {
      if (inTable) {
        inTable = false;
        html += '</tbody></table>';
      }
      if (line.startsWith('#')) {
        const level = (line.match(/^#+/) || ['#'])[0].length;
        const safeText = escapeHtml(line.replace(/^#+\s*/, ''));
        html += `<h${level}>${safeText}</h${level}>`;
      } else if (line.startsWith('*')) {
        const safeText = escapeHtml(line.replace(/^\*\s*/, ''));
        html += `<li>${safeText}</li>`;
      } else if (line) {
        const safeText = escapeHtml(line);
        html += `<p>${safeText}</p>`;
      }
    }
  }
  if (inTable) html += '</tbody></table>';
  return html;
}
</script>

<style>
.backup-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 12px;
}

.backup-alert { margin-bottom: 16px; }
.backup-loading { padding: 20px; }
.backup-table { width: 100%; }

.backup-summary-meta {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  color: var(--text-faint, #6b7280);
  font-size: 13px;
}

.backup-view-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 8px;
  scrollbar-width: thin;
}

.backup-view-tab {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 5px;
  padding: 5px 9px;
  border: 1px solid var(--line-soft, #dbe2ea);
  border-radius: 6px;
  background: var(--surface, #fff);
  color: var(--text-faint, #64748b);
  font: inherit;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.backup-view-tab small {
  color: inherit;
  font-size: 11px;
}

.backup-view-tab--active {
  border-color: #7cb6ff;
  background: #eaf4ff;
  color: #1769aa;
  font-weight: 600;
}

.backup-summary-scroll {
  max-height: 72vh;
  overflow: auto;
  border: 1px solid var(--line-soft, #e5e7eb);
  border-radius: 10px;
}

.backup-summary-table {
  width: max-content;
  min-width: 1450px;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
  line-height: 1.5;
}

.backup-summary-table th,
.backup-summary-table td {
  min-width: 110px;
  max-width: 320px;
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
  border-right: 1px solid var(--line-soft, #e5e7eb);
  border-bottom: 1px solid var(--line-soft, #e5e7eb);
  background: var(--surface, #fff);
}

.backup-summary-table th {
  position: sticky;
  top: 0;
  z-index: 3;
  background: #f7f9fc;
  font-weight: 600;
}

.backup-summary-table th:first-child,
.backup-summary-table td:first-child {
  position: sticky;
  left: 0;
  z-index: 2;
  min-width: 180px;
  max-width: 240px;
}

.backup-summary-table th:first-child { z-index: 4; }
.backup-job-name code { white-space: normal; word-break: break-all; }

.backup-cell-list,
.backup-tag-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.backup-cell-list code { word-break: break-all; }
.backup-muted { color: #9ca3af; }

.backup-category-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.backup-category-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 24px;
  padding: 3px 5px 3px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  color: #344054;
  font: inherit;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition: transform 150ms ease, box-shadow 150ms ease;
}

.backup-category-chip:hover {
  transform: translateY(-1px);
  box-shadow: 0 5px 12px rgb(15 23 42 / 12%);
}

.backup-category-chip b {
  display: grid;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  place-items: center;
  border-radius: 999px;
  background: rgb(255 255 255 / 72%);
  font-size: 10px;
}

.backup-category-chip--env {
  border-color: #99e7db;
  background: #e9fbf7;
  color: #087f6d;
}

.backup-category-chip--params {
  border-color: #b8d7ff;
  background: #eef6ff;
  color: #2463a7;
}

.backup-category-chip--credentials {
  border-color: #ffd59a;
  background: #fff6e8;
  color: #b45309;
}

.backup-category-chip--tools {
  border-color: #d8c4ff;
  background: #f6f0ff;
  color: #6d3dc1;
}

.backup-category-chip--maven {
  border-color: #f5c2b8;
  background: #fff0ed;
  color: #a33a2b;
}

.backup-category-chip--scripts {
  border-color: #b9ddc9;
  background: #edf9f2;
  color: #26734a;
}

.backup-detail-popover,
.backup-script-popover {
  max-width: min(820px, 80vw);
  max-height: 60vh;
  overflow: auto;
  padding: 4px;
}

.backup-detail-popover { width: min(620px, 78vw); }
.backup-detail-popover--compact { width: min(520px, 75vw); padding: 10px; }
.backup-detail-popover section + section { margin-top: 16px; }
.backup-detail-popover h4 { margin: 0 0 8px; font-size: 13px; }

.backup-detail-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid #eef0f3;
}

.backup-detail-row code {
  max-width: 520px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  user-select: text;
}

.backup-detail-row small { display: block; color: #8a919e; }
.backup-credential { padding: 8px 0; }

.backup-credential-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.backup-script-popover { width: min(820px, 82vw); }

.backup-script-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.backup-script-tabs { display: flex; flex-wrap: wrap; gap: 7px; }

.backup-script-tab {
  display: flex;
  min-width: 120px;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 7px 10px;
  border: 1px solid #dfe4ea;
  border-radius: 8px;
  background: #f8fafc;
  color: #475467;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.backup-script-tab strong { font-size: 12px; }
.backup-script-tab small {
  max-width: 210px;
  overflow: hidden;
  color: #98a2b3;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-script-tab--active {
  border-color: #4c8bf5;
  background: #eef5ff;
  color: #175cd3;
  box-shadow: 0 0 0 2px rgb(76 139 245 / 10%);
}

.backup-script-popover pre {
  width: 100%;
  max-height: 440px;
  margin: 0;
  overflow: auto;
  padding: 14px;
  border-radius: 8px;
  background: #111827;
  color: #e5e7eb;
  white-space: pre;
  tab-size: 2;
}

.backup-script-popover code {
  font-family: Consolas, "SFMono-Regular", monospace;
  user-select: text;
}

.markdown-preview {
  max-height: 72vh;
  overflow: auto;
  padding: 10px;
}
</style>
