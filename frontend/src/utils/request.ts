import axios from 'axios';

const getBaseApiUrl = () => {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL as string;
  }
  const base = import.meta.env.BASE_URL || '/';
  const cleanBase = base.endsWith('/') ? base.slice(0, -1) : base;
  return `${cleanBase}/api/v1`;
};

const request = axios.create({
  baseURL: getBaseApiUrl(),
  timeout: 30000,
});

request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

request.interceptors.response.use(
  (response) => {
    // Standard response structure wrapper
    return response;
  },
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('username');
      localStorage.removeItem('role');
      // Redirect to login view hash route
      window.location.hash = '/login';
    }
    const message = error.response?.data?.detail || error.response?.data?.message || error.message || 'API 请求失败。';
    return Promise.reject(new Error(message));
  }
);

export default request;
