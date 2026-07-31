<template>
  <main class="login-shell">
    <section class="login-panel">
      <div class="login-title">
        <img class="brand-logo" src="/logo.svg" alt="Jenkins Logo" />
        <p class="login-subtitle">Jenkins 发布调度中心</p>
      </div>



      <n-alert v-if="error" type="error" :bordered="false" class="login-alert">
        {{ error }}
      </n-alert>

      <n-form ref="formRef" :model="model" :rules="rules" size="large" @submit.prevent="handleLogin">
        <n-form-item label="用户名" path="username">
          <n-input v-model:value="model.username" autocomplete="username" placeholder="username" />
        </n-form-item>
        <n-form-item label="密码" path="password">
          <n-input v-model:value="model.password" type="password" show-password-on="click" autocomplete="current-password" placeholder="password" />
        </n-form-item>
        <n-form-item label="验证码" path="captcha_code">
          <div style="display: flex; gap: 12px; width: 100%;">
            <n-input v-model:value="model.captcha_code" placeholder="请输入验证码" @keydown.enter.prevent="handleLogin" />
            <img v-if="captchaUrl" :src="captchaUrl" @click="fetchCaptcha" style="height: 40px; cursor: pointer; border-radius: 4px; border: 1px solid var(--line-soft);" alt="captcha" title="点击刷新" />
          </div>
        </n-form-item>
        <n-button type="primary" attr-type="submit" block :loading="loading">登录</n-button>

      </n-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { NAlert, NButton, NForm, NFormItem, NInput, useMessage } from 'naive-ui';
import type { FormInst, FormRules } from 'naive-ui';
import { useAuthStore } from '../../store/auth';
import request from '../../utils/request';
import { encryptData } from '../../utils/crypto';

const router = useRouter();
const authStore = useAuthStore();
const message = useMessage();
const formRef = ref<FormInst | null>(null);
const loading = ref(false);
const error = ref('');

const model = ref({ username: '', password: '', captcha_id: '', captcha_code: '' });
const captchaUrl = ref('');

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  captcha_code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
};

async function fetchCaptcha() {
  try {
    const res = await request.get('/auth/captcha');
    model.value.captcha_id = res.data.captcha_id;
    captchaUrl.value = res.data.image_base64;
    model.value.captcha_code = ''; // reset on fetch
  } catch (e) {
    console.error('Failed to fetch captcha', e);
  }
}

onMounted(() => {
  fetchCaptcha();
});


function handleLogin() {
  formRef.value?.validate(async (errors) => {
    if (errors) return;
    loading.value = true;
    error.value = '';
    try {
      const encryptedUsername = await encryptData(model.value.username);
      const encryptedPassword = await encryptData(model.value.password);
      const formData = new URLSearchParams();
      formData.append('username', encryptedUsername);
      formData.append('password', encryptedPassword);
      formData.append('captcha_id', model.value.captcha_id);
      formData.append('captcha_code', model.value.captcha_code);
      const response = await request.post('/auth/login', formData, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      const data = response.data;
      authStore.setAuth(data.access_token, data.username, data.role);
      message.success('登录成功');
      router.push('/dashboard');
    } catch (err: any) {
      error.value = err.message || '登录失败，请检查账号和密码';
      fetchCaptcha(); // 登录失败时刷新验证码
    } finally {
      loading.value = false;
    }
  });
}
</script>

<style scoped>
.login-shell {
  min-width: 1024px;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 40px;
  background: #f5f5f7;
}

.login-panel {
  width: 420px;
  padding: 40px;
  border: 1px solid var(--line-soft);
  border-radius: 20px;
  background: #fff;
  box-shadow: var(--shadow-md);
}

.login-title {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
  text-align: center;
}

.brand-logo {
  width: 64px;
  height: 64px;
  object-fit: contain;
}

.login-subtitle {
  margin: 0;
  color: var(--text-muted);
  font-size: 14px;
}

.login-alert {
  margin-bottom: 16px;
}
</style>
