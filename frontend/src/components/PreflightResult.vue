<template>
  <n-card size="small" title="发布前检查" aria-label="发布前检查结果">
    <template v-if="result">
      <p v-if="result.summary">{{ result.summary }}</p>
      <p class="muted mono">检查时间：{{ formatDateTime(checkedAt) }}</p>
      <ul v-if="tasks.length" class="preflight-tasks">
        <li v-for="(task, index) in tasks" :key="task.task_id ?? task.job_name ?? index" class="preflight-task">
          <strong>{{ task.job_name || '未命名任务' }}</strong>
          <n-tag size="small" :type="tagType(task.status)">{{ getPreflightMeta(task.status).label }}</n-tag>
          <ul v-if="task.checks?.length" class="preflight-checks">
            <li v-for="(check, index) in task.checks" :key="`${check.code || 'check'}-${index}`">
              <n-tag size="small" :type="tagType(check.status)">{{ getPreflightMeta(check.status).label }}</n-tag>
              {{ check.message || '无检查信息' }}
            </li>
          </ul>
          <span v-else class="muted">无检查项</span>
        </li>
      </ul>
      <n-empty v-else description="暂无检查任务" size="small" />
    </template>
    <n-empty v-else description="暂无检查结果" size="small" />
  </n-card>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { NCard, NEmpty, NTag } from 'naive-ui';
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

const props = defineProps<{
  result?: PreflightResultData | null;
  checkedAt?: string | null;
}>();

const tasks = computed(() => props.result?.tasks || []);

function tagType(status?: string | null): 'default' | 'success' | 'warning' | 'error' {
  const tone = getPreflightMeta(status).tone;
  if (tone === 'success' || tone === 'warning') return tone;
  return tone === 'danger' ? 'error' : 'default';
}
</script>

<style scoped>
.preflight-tasks,
.preflight-checks {
  margin: 8px 0 0;
  padding-left: 20px;
}

.preflight-task + .preflight-task {
  margin-top: 8px;
}

.preflight-checks li + li {
  margin-top: 4px;
}
</style>
