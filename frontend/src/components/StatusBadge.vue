<template>
  <span
    class="status-badge"
    :class="[
      `status-badge--${meta.tone}`,
      glowClass
    ]"
  >
    <span class="status-badge__dot" :class="dotGlowClass"></span>
    <span class="status-badge__label">{{ meta.label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { getStatusMeta } from '../utils/release-ui';

const props = defineProps<{
  status?: string | null;
  buildNumber?: number | null;
}>();

const meta = computed(() => getStatusMeta(props.status, props.buildNumber));

// Inject UIverse Glow Keyframe Classes based on Tone
const glowClass = computed(() => {
  switch (meta.value.tone) {
    case 'success':
      return 'uiverse-badge-glow-success';
    case 'running':
      return 'uiverse-badge-glow-running';
    case 'danger':
      return 'uiverse-badge-glow-danger';
    default:
      return '';
  }
});

const dotGlowClass = computed(() => {
  if (meta.value.tone === 'running') {
    return 'uiverse-glow-running';
  }
  return '';
});
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 2px 10px;
  border-radius: 20px;
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.25s ease;
  backdrop-filter: blur(8px);
}

.status-badge__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.status-badge--success {
  color: #15803d;
  background: rgba(220, 252, 231, 0.85);
  border: 1px solid rgba(34, 197, 94, 0.3);
}

.status-badge--danger {
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.85);
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.status-badge--running {
  color: #1d4ed8;
  background: rgba(219, 234, 254, 0.85);
  border: 1px solid rgba(59, 130, 246, 0.35);
}

.status-badge--warning {
  color: #c2410c;
  background: rgba(255, 237, 213, 0.85);
  border: 1px solid rgba(249, 115, 22, 0.3);
}

.status-badge--disabled {
  color: #4b5563;
  background: rgba(243, 244, 246, 0.85);
  border: 1px solid rgba(156, 163, 175, 0.25);
}

.status-badge--info {
  color: #0369a1;
  background: rgba(224, 242, 254, 0.85);
  border: 1px solid rgba(14, 165, 233, 0.3);
}

.status-badge--neutral {
  color: #475569;
  background: rgba(241, 245, 249, 0.85);
  border: 1px solid rgba(148, 163, 184, 0.3);
}

/* UIverse Enhancements */
.uiverse-badge-glow-success {
  box-shadow: 0 0 10px rgba(34, 197, 94, 0.25);
}
.uiverse-badge-glow-danger {
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.25);
}
.uiverse-badge-glow-running {
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.35);
}
</style>
