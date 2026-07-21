<template>
  <div class="app-shell" :class="{ 'app-shell--collapsed': isCollapsed }">
    <aside class="side-nav">
      <RouterLink to="/dashboard" class="brand">
        <img class="brand__logo" src="/logo.svg" alt="Jenkins Logo" />
        <span class="brand__text">发布调度中心</span>
      </RouterLink>

      <nav class="nav-list" aria-label="主导航">
        <n-tooltip
          v-for="item in navItems"
          :key="item.path"
          placement="right"
          :disabled="!isCollapsed"
        >
          <template #trigger>
            <RouterLink
              :to="item.path"
              class="nav-link"
              :class="{ 'nav-link--active': isActive(item.path) }"
            >
              <n-icon :component="item.icon" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </template>
          <span>{{ item.label }}</span>
        </n-tooltip>
      </nav>
    </aside>

    <!-- 侧边栏右侧外边缘的浮动折叠拉手 -->
    <button
      class="sidebar-trigger"
      type="button"
      @click="toggleCollapse"
      :aria-label="isCollapsed ? '展开菜单' : '折叠菜单'"
    >
      <n-icon :component="isCollapsed ? ChevronForwardOutline : ChevronBackOutline" />
    </button>

    <div class="app-main">
      <header class="topbar">
        <div class="topbar__title">
          <span class="topbar__section">控制台</span>
          <strong>{{ currentRouteTitle }}</strong>
        </div>

        <button class="logout-button" type="button" @click="confirmLogout">
          <n-icon :component="LogOutOutline" aria-hidden="true" />
          <span>退出</span>
        </button>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NIcon, NTooltip, useDialog } from 'naive-ui';
import {
  BuildOutline,
  FileTrayFullOutline,
  GitNetworkOutline,
  HomeOutline,
  LogOutOutline,
  SettingsOutline,
  ChevronBackOutline,
  ChevronForwardOutline,
} from '@vicons/ionicons5';
import { useAuthStore } from '../store/auth';
import request from '../utils/request';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const dialog = useDialog();

const isCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true');

function toggleCollapse() {
  isCollapsed.value = !isCollapsed.value;
  localStorage.setItem('sidebar-collapsed', String(isCollapsed.value));
}

const navItems = [
  { label: '仪表盘', path: '/dashboard', icon: HomeOutline },
  { label: 'Jenkins 服务', path: '/jenkins', icon: BuildOutline },
  { label: '发布计划', path: '/release', icon: GitNetworkOutline },
  { label: '执行历史', path: '/history', icon: FileTrayFullOutline },
  { label: '系统配置', path: '/config', icon: SettingsOutline },
];

const routeTitles: Record<string, string> = {
  Dashboard: '仪表盘',
  Jenkins: 'Jenkins 集成',
  Release: '发布计划',
  ReleaseDetail: '发布详情',
  History: '执行历史',
  Config: '系统配置',
};

const currentRouteTitle = computed(() => routeTitles[String(route.name)] || '仪表盘');

function isActive(path: string) {
  return route.path === path || (path !== '/dashboard' && route.path.startsWith(`${path}/`));
}

function confirmLogout() {
  dialog.warning({
    title: '退出登录',
    content: '确认退出当前账号？',
    positiveText: '退出',
    negativeText: '取消',
    onPositiveClick: handleLogout,
  });
}

async function handleLogout() {
  try {
    await request.post('/auth/logout');
  } catch (err) {
    console.error('Logout request failed:', err);
  } finally {
    authStore.logout();
    router.push('/login');
  }
}
</script>

<style scoped>
.app-shell {
  min-width: 1024px;
  min-height: 100dvh;
  display: flex;
  background: var(--bg);
}

.side-nav {
  position: sticky;
  top: 0;
  height: 100dvh;
  width: 232px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: rgba(238, 238, 240, 0.82);
  color: var(--text-muted);
  border-right: 1px solid var(--line-soft);
  backdrop-filter: saturate(180%) blur(24px);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  overflow: hidden;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.app-shell--collapsed .side-nav {
  width: 64px;
}

.brand {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line-soft);
  transition: padding 0.3s cubic-bezier(0.4, 0, 0.2, 1), gap 0.3s ease;
}

.brand:hover {
  color: inherit;
}

.app-shell--collapsed .brand {
  padding: 0 16px;
  justify-content: center;
  gap: 0;
}

.brand__logo {
  width: 32px;
  height: 32px;
  object-fit: contain;
}

.brand__text {
  color: var(--text);
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
  white-space: nowrap;
  transition: opacity 0.2s ease, max-width 0.2s ease;
  opacity: 1;
  max-width: 150px;
  display: inline-block;
}

.app-shell--collapsed .brand__text {
  opacity: 0;
  max-width: 0;
  pointer-events: none;
  overflow: hidden;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 10px;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 12px;
  border-radius: 10px;
  color: var(--text-muted);
  font-weight: 500;
  transition: color 0.16s ease, background 0.16s ease, transform 0.16s ease, padding 0.3s cubic-bezier(0.4, 0, 0.2, 1), gap 0.3s ease, justify-content 0.3s ease;
}

.app-shell--collapsed .nav-link {
  padding: 0;
  justify-content: center;
  gap: 0;
}

.nav-link :deep(.n-icon) {
  font-size: 18px;
}

.nav-link span {
  white-space: nowrap;
  transition: opacity 0.2s ease, max-width 0.2s ease;
  opacity: 1;
  max-width: 150px;
  display: inline-block;
}

.app-shell--collapsed .nav-link span {
  opacity: 0;
  max-width: 0;
  pointer-events: none;
  overflow: hidden;
}

.nav-link:hover {
  background: rgba(255, 255, 255, 0.64);
  color: var(--text);
}

.nav-link:active {
  transform: scale(0.985);
}

.nav-link--active {
  background: rgba(0, 113, 227, 0.12);
  color: var(--text);
  font-weight: 600;
}

.nav-link--active :deep(.n-icon) {
  color: var(--primary);
}

.app-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 5;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 28px;
  background: rgba(245, 245, 247, 0.78);
  border-bottom: 1px solid var(--line-soft);
  backdrop-filter: saturate(180%) blur(24px);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
}

.sidebar-trigger {
  position: fixed;
  left: 232px;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 99;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ffffff;
  border: 1px solid var(--line-soft);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--text-muted);
  opacity: 0.3;
  transition: left 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.16s ease, background-color 0.16s ease, border-color 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.sidebar-trigger :deep(.n-icon) {
  font-size: 12px;
}

.side-nav:hover + .sidebar-trigger,
.sidebar-trigger:hover {
  opacity: 1;
  color: var(--primary);
  border-color: var(--primary);
  box-shadow: 0 4px 10px rgba(0, 113, 227, 0.15);
  transform: translate(-50%, -50%) scale(1.08);
}

.sidebar-trigger:active {
  transform: translate(-50%, -50%) scale(0.95);
}

.app-shell--collapsed .sidebar-trigger {
  left: 64px;
}

.topbar__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.topbar__section {
  color: var(--text-faint);
}

.topbar__section::after {
  content: '/';
  margin-left: 8px;
  color: var(--line);
}

.topbar__title strong {
  color: var(--text);
  font-weight: 600;
}

.logout-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.82);
  color: var(--text-muted);
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.logout-button :deep(.n-icon) {
  font-size: 16px;
}

.logout-button:hover {
  border-color: var(--line);
  background: #fff;
  color: var(--text);
}

.logout-button:active {
  transform: scale(0.98);
}

.logout-button:focus-visible {
  outline: 2px solid rgba(0, 113, 227, 0.35);
  outline-offset: 2px;
}

.content {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
  padding: 28px 32px 40px;
  flex: 1;
}
</style>
