<template>
  <div
    class="uiverse-card"
    :class="[
      `uiverse-card--${variant}`,
      { 'uiverse-card--hoverable': hoverable }
    ]"
  >
    <div v-if="$slots.header || title" class="uiverse-card__header">
      <slot name="header">
        <h3 class="uiverse-card__title">{{ title }}</h3>
        <p v-if="subtitle" class="uiverse-card__subtitle">{{ subtitle }}</p>
      </slot>
    </div>
    <div class="uiverse-card__body">
      <slot></slot>
    </div>
    <div v-if="$slots.footer" class="uiverse-card__footer">
      <slot name="footer"></slot>
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'glass' | 'cyber' | 'gradient' | 'clean';
    title?: string;
    subtitle?: string;
    hoverable?: boolean;
  }>(),
  {
    variant: 'glass',
    hoverable: true,
  }
);
</script>

<style scoped>
.uiverse-card {
  position: relative;
  border-radius: 16px;
  padding: 18px 22px;
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  overflow: hidden;
}

.uiverse-card--glass {
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.04);
}

.uiverse-card--cyber {
  background: #0d1117;
  color: #e6edf3;
  border: 1px solid rgba(56, 189, 248, 0.25);
  box-shadow: 0 0 20px rgba(56, 189, 248, 0.08);
}
.uiverse-card--cyber .uiverse-card__title {
  color: #38bdf8;
}

.uiverse-card--gradient {
  background: linear-gradient(135deg, #ffffff 0%, #f4f7fa 100%);
  border: 1px solid rgba(0, 113, 227, 0.15);
  box-shadow: 0 10px 25px rgba(0, 113, 227, 0.06);
}

.uiverse-card--clean {
  background: #ffffff;
  border: 1px solid var(--line-soft, rgba(60, 60, 67, 0.12));
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
}

/* Hover effect */
.uiverse-card--hoverable:hover {
  transform: translateY(-3px);
}
.uiverse-card--glass.uiverse-card--hoverable:hover {
  box-shadow: 0 12px 35px rgba(0, 113, 227, 0.12);
  border-color: rgba(0, 113, 227, 0.3);
}
.uiverse-card--cyber.uiverse-card--hoverable:hover {
  border-color: #38bdf8;
  box-shadow: 0 0 25px rgba(56, 189, 248, 0.2);
}

/* Internal Layout */
.uiverse-card__header {
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(60, 60, 67, 0.08);
}
.uiverse-card--cyber .uiverse-card__header {
  border-bottom-color: rgba(255, 255, 255, 0.1);
}

.uiverse-card__title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.uiverse-card__subtitle {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--text-muted, #6e6e73);
}

.uiverse-card__body {
  font-size: 13.5px;
  line-height: 1.5;
}

.uiverse-card__footer {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid rgba(60, 60, 67, 0.08);
}
</style>
