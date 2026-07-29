import { createRouter, createWebHistory } from 'vue-router';
import type { RouteRecordRaw } from 'vue-router';
import MainLayout from '../views/MainLayout.vue';
import Login from '../views/login/index.vue';

const routes: Array<RouteRecordRaw> = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
  },
  {
    path: '/',
    component: MainLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/dashboard/index.vue'),
      },
      {
        path: 'jenkins',
        name: 'Jenkins',
        component: () => import('../views/jenkins/index.vue'),
      },
      {
        path: 'release',
        name: 'Release',
        component: () => import('../views/release/index.vue'),
      },
      {
        path: 'release/:id',
        name: 'ReleaseDetail',
        component: () => import('../views/release/detail.vue'),
      },
      {
        path: 'history',
        name: 'History',
        component: () => import('../views/history/index.vue'),
      },
      {
        path: 'config',
        name: 'Config',
        component: () => import('../views/config/index.vue'),
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token');
  if (to.name !== 'Login' && !token) {
    next({ name: 'Login' });
    return;
  }
  if (to.name === 'Login' && token) {
    next({ name: 'Dashboard' });
    return;
  }
  next();
});

export default router;
