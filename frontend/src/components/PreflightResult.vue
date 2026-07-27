<template>
  <div class="preflight-container">
    <!-- 详情页场景：开启折叠面板 -->
    <n-collapse v-if="collapsible" :default-expanded-names="[]">
      <n-collapse-item name="preflight" title="检测">
        <template #header-extra>
          <div class="header-extra-wrap">
            <span v-if="checkedAt" class="muted mono text-xs">
              {{ formatDateTime(checkedAt) }}
            </span>
          </div>
        </template>
        <div class="preflight-content">
          <template v-if="result">
            <p v-if="result.summary" class="preflight-summary">{{ result.summary }}</p>
            <ul v-if="tasks.length" class="preflight-tasks">
              <li v-for="(task, index) in tasks" :key="task.task_id ?? task.job_name ?? index" class="preflight-task">
                <div class="task-head">
                  <strong>{{ task.job_name || '未命名任务' }}</strong>
                  <n-tag size="small" :type="tagType(task.status)">{{ getPreflightMeta(task.status).label }}</n-tag>
                </div>
                <ul v-if="task.checks?.length" class="preflight-checks">
                  <li v-for="(check, cIdx) in task.checks" :key="`${check.code || 'check'}-${cIdx}`">
                    <n-tag size="small" :type="tagType(check.status)">{{ getPreflightMeta(check.status).label }}</n-tag>
                    <span class="check-msg">{{ check.message || '无检查信息' }}</span>
                  </li>
                </ul>
                <span v-else class="muted">无检查项</span>
              </li>
            </ul>
            <n-empty v-else description="暂无检查任务" size="small" />
          </template>
          <n-empty v-else description="暂无检查结果" size="small" />
        </div>
      </n-collapse-item>
    </n-collapse>

    <!-- 弹窗等常规场景：直观平铺展示 -->
    <div v-else class="preflight-content">
      <template v-if="result">
        <p v-if="result.summary" class="preflight-summary">{{ result.summary }}</p>
        <ul v-if="tasks.length" class="preflight-tasks">
          <li v-for="(task, index) in tasks" :key="task.task_id ?? task.job_name ?? index" class="preflight-task">
            <div class="task-head">
              <strong>{{ task.job_name || '未命名任务' }}</strong>
              <n-tag size="small" :type="tagType(task.status)">{{ getPreflightMeta(task.status).label }}</n-tag>
            </div>
            <ul v-if="task.checks?.length" class="preflight-checks">
              <li v-for="(check, cIdx) in task.checks" :key="`${check.code || 'check'}-${cIdx}`">
                <n-tag size="small" :type="tagType(check.status)">{{ getPreflightMeta(check.status).label }}</n-tag>
                <span class="check-msg">{{ check.message || '无检查信息' }}</span>
              </li>
            </ul>
            <span v-else class="muted">无检查项</span>
          </li>
        </ul>
        <n-empty v-else description="暂无检查任务" size="small" />
      </template>
      <n-empty v-else description="暂无检查结果" size="small" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { NCollapse, NCollapseItem, NEmpty, NTag } from 'naive-ui';
import { formatDateTime, getPreflightMeta } from '../utils/release-ui';

type PreflightCheck = {
  code?: string | null;
  status?: string | null;
  message?: string | null;
};

type PreflightTask = {
  task_id?: number | string | null;
  job_name?: string | null;
  status?: string | null;
  checks?: PreflightCheck[] | null;
};

type PreflightResultData = {
  summary?: string | null;
  tasks?: PreflightTask[] | null;
};

const props = withDefaults(
  defineProps<{
    result?: PreflightResultData | null;
    checkedAt?: string | null;
    collapsible?: boolean;
  }>(),
  {
    collapsible: false,
  }
);

const tasks = computed(() => props.result?.tasks || []);

function tagType(status?: string | null): 'default' | 'success' | 'warning' | 'error' {
  const tone = getPreflightMeta(status).tone;
  if (tone === 'success' || tone === 'warning') return tone;
  return tone === 'danger' ? 'error' : 'default';
}
</script>

<style scoped>
.preflight-container {
  margin-bottom: 16px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--bg-card, #ffffff);
  padding: 4px 14px;
}

.header-extra-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preflight-content {
  padding: 8px 4px 12px;
}

.preflight-summary {
  margin-bottom: 10px;
  font-weight: 500;
  color: var(--text-main);
}

.preflight-tasks,
.preflight-checks {
  margin: 8px 0 0;
  padding-left: 0;
  list-style: none;
}

.preflight-task {
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--bg-subtle, #f9fafb);
  margin-bottom: 8px;
}

.task-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.preflight-checks li {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 13px;
}
</style>
