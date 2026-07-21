<template>
  <n-button
    v-bind="$attrs"
    class="refresh-button"
    :disabled="disabled || loading"
    :aria-busy="loading"
    :aria-label="label"
  >
    <span :class="{ 'refresh-button__label--loading': loading }">{{ label }}</span>
    <span v-if="loading" class="refresh-button__spinner" aria-hidden="true">
      <n-spin :size="12" />
    </span>
  </n-button>
</template>

<script setup lang="ts">
import { NButton, NSpin } from 'naive-ui';

defineOptions({ inheritAttrs: false });

withDefaults(defineProps<{
  label: string;
  loading?: boolean;
  disabled?: boolean;
}>(), {
  loading: false,
  disabled: false,
});
</script>

<style scoped>
.refresh-button {
  position: relative;
}

.refresh-button__label--loading {
  visibility: hidden;
}

.refresh-button__spinner {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: inherit;
  pointer-events: none;
}

.refresh-button__spinner :deep(.n-spin) {
  --n-color: currentColor !important;
}
</style>
