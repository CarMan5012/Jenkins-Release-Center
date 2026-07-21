import { defineStore } from 'pinia';
import { ref } from 'vue';

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('token') || '');
  const username = ref<string>(localStorage.getItem('username') || '');
  const role = ref<string>(localStorage.getItem('role') || '');

  const setAuth = (newToken: string, newUsername: string, newRole: string) => {
    token.value = newToken;
    username.value = newUsername;
    role.value = newRole;
    localStorage.setItem('token', newToken);
    localStorage.setItem('username', newUsername);
    localStorage.setItem('role', newRole);
  };

  const logout = () => {
    token.value = '';
    username.value = '';
    role.value = '';
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('role');
  };

  return { token, username, role, setAuth, logout };
});
