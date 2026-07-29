<template>
  <button
    class="uiverse-btn"
    :class="[
      `uiverse-btn--${variant}`,
      `uiverse-btn--${size}`,
      { 'uiverse-btn--loading': loading, 'uiverse-btn--disabled': disabled }
    ]"
    :disabled="disabled || loading"
    @click="handleClick"
  >
    <span class="uiverse-btn__glow"></span>
    <span class="uiverse-btn__content">
      <span v-if="loading" class="uiverse-btn__spinner"></span>
      <slot></slot>
    </span>
  </button>
</template>

<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'success' | 'warning' | 'danger' | 'cyber';
    size?: 'sm' | 'md' | 'lg';
    loading?: boolean;
    disabled?: boolean;
  }>(),
  {
    variant: 'primary',
    size: 'md',
    loading: false,
    disabled: false,
  }
);

const emit = defineEmits<{
  (e: 'click', event: MouseEvent): void;
}>();

const handleClick = (e: MouseEvent) => {
  if (!props.disabled && !props.loading) {
    emit('click', e);
  }
};
</script>

<style scoped>
.uiverse-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  outline: none;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
  overflow: hidden;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
  letter-spacing: 0.02em;
}

/* Sizes */
.uiverse-btn--sm {
  padding: 3px 10px;
  font-size: 11px;
  height: 24px;
  border-radius: 6px;
}
.uiverse-btn--md {
  padding: 4px 12px;
  font-size: 12px;
  height: 28px;
  border-radius: 7px;
}
.uiverse-btn--lg {
  padding: 6px 16px;
  font-size: 13px;
  height: 34px;
  border-radius: 8px;
}

/* Variants */
.uiverse-btn--primary {
  background: linear-gradient(135deg, #0071e3 0%, #0052b4 100%);
  color: #ffffff;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.35);
}
.uiverse-btn--primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 113, 227, 0.5);
  background: linear-gradient(135deg, #007bfd 0%, #0060d4 100%);
}

.uiverse-btn--success {
  background: linear-gradient(135deg, #248a3d 0%, #1a652c 100%);
  color: #ffffff;
  box-shadow: 0 4px 14px rgba(36, 138, 61, 0.35);
}
.uiverse-btn--success:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(36, 138, 61, 0.5);
}

.uiverse-btn--warning {
  background: linear-gradient(135deg, #e67e22 0%, #d35400 100%);
  color: #ffffff;
  box-shadow: 0 4px 14px rgba(230, 126, 34, 0.35);
}
.uiverse-btn--warning:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(230, 126, 34, 0.5);
}

.uiverse-btn--danger {
  background: linear-gradient(135deg, #d70015 0%, #a30010 100%);
  color: #ffffff;
  box-shadow: 0 4px 14px rgba(215, 0, 21, 0.35);
}
.uiverse-btn--danger:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(215, 0, 21, 0.5);
}

.uiverse-btn--cyber {
  background: #0f172a;
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.4);
  box-shadow: 0 0 15px rgba(56, 189, 248, 0.2);
}
.uiverse-btn--cyber:hover:not(:disabled) {
  transform: translateY(-2px);
  border-color: #38bdf8;
  box-shadow: 0 0 25px rgba(56, 189, 248, 0.5);
  color: #ffffff;
}

/* Active & Disabled State */
.uiverse-btn:active:not(:disabled) {
  transform: translateY(0) scale(0.98);
}
.uiverse-btn--disabled {
  opacity: 0.55;
  cursor: not-allowed;
  box-shadow: none !important;
}

/* Internal Glow & Spinner */
.uiverse-btn__content {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
}

.uiverse-btn__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
