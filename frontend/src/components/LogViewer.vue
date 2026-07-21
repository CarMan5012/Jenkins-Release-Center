<template>
  <section class="log-viewer">
    <div class="log-viewer__bar">
      <div>
        <strong class="log-viewer__title">{{ title }}</strong>
        <span v-if="buildNumber" class="log-viewer__build muted mono"> #{{ buildNumber }}</span>
      </div>
      <div class="log-viewer__actions">
        <n-tag :bordered="false" size="small" :type="hasMore ? 'warning' : 'success'">
          {{ hasMore ? '跟随中' : '已停止' }}
        </n-tag>
        <RefreshButton size="small" secondary label="刷新" :loading="manualRefreshing" :disabled="!taskId && !historyId" @click="refresh" />
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" class="state-alert">
      {{ error }}
    </n-alert>

    <div
      ref="terminalRef"
      class="log-console"
      :class="{ 'log-console--empty': !lines.length }"
      role="log"
      aria-live="polite"
      :aria-label="title"
    >
      <template v-if="loading && !lines.length">
        <n-skeleton text :repeat="8" />
      </template>
      <template v-else-if="!taskId && !historyId">
        <div class="empty-inline">请选择一条任务日志。</div>
      </template>
      <template v-else-if="!lines.length">
        <div class="empty-inline">暂无日志输出。</div>
      </template>
      <pre v-else class="log-console__text"><span
        v-for="(line, index) in lines"
        :key="index"
        :class="lineClass(line)"
      >{{ line || ' ' }}</span></pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { NAlert, NSkeleton, NTag } from 'naive-ui';
import RefreshButton from './RefreshButton.vue';
import request from '../utils/request';

const props = withDefaults(defineProps<{
  taskId?: number | null;
  historyId?: number | null;
  title?: string;
  buildNumber?: number | null;
  auto?: boolean;
}>(), {
  taskId: null,
  historyId: null,
  title: 'Console 日志',
  buildNumber: null,
  auto: true,
});

const terminalRef = ref<HTMLDivElement | null>(null);
const logBuffer = ref('');
const loading = ref(false);
const manualRefreshing = ref(false);
const error = ref('');
const hasMore = ref(false);
let offset = 0;
let poller: ReturnType<typeof setTimeout> | null = null;

const lines = computed(() => logBuffer.value ? logBuffer.value.split(/\r?\n/) : []);

function clearPoller() {
  if (poller) {
    clearTimeout(poller);
    poller = null;
  }
}

async function scrollToBottom() {
  await nextTick();
  if (terminalRef.value) terminalRef.value.scrollTop = terminalRef.value.scrollHeight;
}

async function pollLogs() {
  if (!props.taskId && !props.historyId) return;
  loading.value = true;
  error.value = '';
  try {
    const url = props.historyId 
      ? `/history/${props.historyId}/logs` 
      : `/history/tasks/${props.taskId}/logs`;
    const res = await request.get(url, {
      params: { start: offset },
    });
    const data = res.data;
    logBuffer.value = offset === 0 ? data.log_text : `${logBuffer.value}${data.log_text}`;
    offset = data.next_start;
    hasMore.value = data.has_more;
    await scrollToBottom();
    if (data.has_more) poller = setTimeout(pollLogs, 2000);
  } catch (err: any) {
    error.value = err.message || '日志加载失败。';
    hasMore.value = false;
  } finally {
    loading.value = false;
  }
}

async function restart() {
  clearPoller();
  offset = 0;
  logBuffer.value = '';
  hasMore.value = false;
  if (props.taskId || props.historyId) await pollLogs();
}

async function refresh() {
  manualRefreshing.value = true;
  const startedAt = Date.now();
  try {
    await restart();
  } finally {
    const remaining = 500 - (Date.now() - startedAt);
    if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
    manualRefreshing.value = false;
  }
}

function lineClass(line: string) {
  const value = line.toLowerCase();
  if (value.includes('error') || value.includes('failed') || value.includes('exception')) return 'log-line log-line--error';
  if (value.includes('warn') || value.includes('unstable')) return 'log-line log-line--warn';
  if (value.includes('success') || value.includes('finished: success')) return 'log-line log-line--success';
  return 'log-line';
}

watch([() => props.taskId, () => props.historyId], () => {
  void restart();
}, { immediate: props.auto });

onUnmounted(clearPoller);
</script>

<style scoped>
.log-viewer__title {
  font-size: 14px;
  font-weight: 600;
}

.log-viewer__build {
  margin-left: 4px;
  font-size: 12px;
}
</style>
