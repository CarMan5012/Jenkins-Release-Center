<template>
  <n-config-provider
    :theme="null"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-loading-bar-provider>
      <n-message-provider>
        <n-notification-provider>
          <n-dialog-provider>
            <router-view />
          </n-dialog-provider>
        </n-notification-provider>
      </n-message-provider>
    </n-loading-bar-provider>
    <n-global-style />
  </n-config-provider>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from './store/auth';
import request from './utils/request';

import {
  dateZhCN,
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NLoadingBarProvider,
  NMessageProvider,
  NNotificationProvider,
  zhCN,
} from 'naive-ui';
import type { GlobalThemeOverrides } from 'naive-ui';

const router = useRouter();
const authStore = useAuthStore();

let idleTimer: number | null = null;
const IDLE_TIMEOUT = 30 * 60 * 1000; // 30 minutes

function resetIdleTimer() {
  if (idleTimer) {
    window.clearTimeout(idleTimer);
  }
  if (authStore.token) {
    idleTimer = window.setTimeout(handleIdleTimeout, IDLE_TIMEOUT);
  }
}

async function handleIdleTimeout() {
  if (authStore.token) {
    try {
      await request.post('/auth/logout');
    } catch (e) {
      console.error('Auto logout error:', e);
    } finally {
      authStore.logout();
      router.push('/login');
    }
  }
}

const activityEvents = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart'];

onMounted(() => {
  activityEvents.forEach(event => {
    window.addEventListener(event, resetIdleTimer, { passive: true });
  });
  resetIdleTimer();
});

onUnmounted(() => {
  activityEvents.forEach(event => {
    window.removeEventListener(event, resetIdleTimer);
  });
  if (idleTimer) window.clearTimeout(idleTimer);
});


const themeOverrides: GlobalThemeOverrides = {

  common: {
    primaryColor: '#0071e3',
    primaryColorHover: '#147ce5',
    primaryColorPressed: '#0062c3',
    infoColor: '#0071e3',
    successColor: '#34c759',
    warningColor: '#ff9f0a',
    errorColor: '#ff3b30',
    bodyColor: '#f5f5f7',
    cardColor: '#ffffff',
    modalColor: '#ffffff',
    popoverColor: '#ffffff',
    borderColor: 'rgba(60, 60, 67, 0.16)',
    textColorBase: '#1d1d1f',
    textColor1: '#1d1d1f',
    textColor2: '#6e6e73',
    textColor3: '#8e8e93',
    borderRadius: '10px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif',
    fontFamilyMono: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
  },
  Button: {
    borderRadiusTiny: '6px',
    borderRadiusSmall: '7px',
    borderRadiusMedium: '9px',
    borderRadiusLarge: '11px',
  },
  Card: {
    borderRadius: '14px',
  },
  Input: {
    borderRadius: '9px',
    boxShadowFocus: '0 0 0 3px rgba(0, 113, 227, .14)',
  },
  Select: {
    peers: {
      InternalSelection: {
        borderRadius: '9px',
        boxShadowFocus: '0 0 0 3px rgba(0, 113, 227, .14)',
      },
    },
  },
  DataTable: {
    thColor: '#fafafa',
    tdColor: '#ffffff',
    borderColor: 'rgba(60, 60, 67, 0.10)',
  },
};
</script>
