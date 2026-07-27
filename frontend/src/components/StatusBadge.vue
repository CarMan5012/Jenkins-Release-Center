<template>
  <span class="status-badge" :class="`status-badge--${meta.tone}`">
    <span class="status-badge__dot"></span>
    {{ meta.label }}
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
</script>
