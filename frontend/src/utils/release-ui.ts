export type StatusTone = 'success' | 'danger' | 'running' | 'warning' | 'disabled' | 'info' | 'neutral';

export type StatusMeta = {
  label: string;
  tone: StatusTone;
};

export type PreflightStatus = 'UNCHECKED' | 'PASSED' | 'WARNING' | 'FAILED';

export type PlanLike = {
  name: string;
  status: string;
  type: string;
  created_at?: string;
  execute_time?: string | null;
  tasks?: Array<any>;
};

const STATUS_META: Record<string, StatusMeta> = {
  SUCCESS: { label: '成功', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  FAILURE: { label: '失败', tone: 'danger' },
  RUNNING: { label: '运行中', tone: 'running' },
  WAITING: { label: '等待中', tone: 'info' },
  CANCELLED: { label: '已取消', tone: 'disabled' },
  DISABLED: { label: '已禁用', tone: 'disabled' },
  UNSTABLE: { label: '不稳定', tone: 'warning' },
  ABORTED: { label: '已中止', tone: 'warning' },
  NOT_BUILT: { label: '未构建', tone: 'neutral' },
};

const PREFLIGHT_META: Record<PreflightStatus, StatusMeta> = {
  UNCHECKED: { label: '未检查', tone: 'neutral' },
  PASSED: { label: '通过', tone: 'success' },
  WARNING: { label: '警告', tone: 'warning' },
  FAILED: { label: '未通过', tone: 'danger' },
};

export function getStatusMeta(status?: string | null): StatusMeta {
  if (!status) return { label: '未知', tone: 'neutral' };
  return STATUS_META[status] || { label: status, tone: 'neutral' };
}

export function getPreflightMeta(status?: string | null): StatusMeta {
  return PREFLIGHT_META[status as PreflightStatus] || PREFLIGHT_META.UNCHECKED;
}

export function isPreflightBlocked(status?: string | null): boolean {
  return status !== 'PASSED' && status !== 'WARNING';
}

export function formatPlanType(type?: string | null): string {
  const mapping: Record<string, string> = {
    IMMEDIATE: '立即执行',
    SCHEDULED: '定时执行',
    BATCH: '批量调度',
    PIPELINE: '流水线串行',
  };
  return type ? mapping[type] || type : '-';
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '-';
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
}

export function getPlanDurationSeconds(plan: PlanLike): number | null {
  const durations = plan.tasks?.map((task) => task.duration || 0).filter((duration) => duration > 0) || [];
  if (durations.length) return durations.reduce((sum, duration) => sum + duration, 0);

  const starts = plan.tasks?.map((task) => task.started_at ? new Date(task.started_at).getTime() : NaN).filter(Number.isFinite) || [];
  const finishes = plan.tasks?.map((task) => task.finished_at ? new Date(task.finished_at).getTime() : NaN).filter(Number.isFinite) || [];
  if (!starts.length || !finishes.length) return null;
  return Math.max(0, Math.round((Math.max(...finishes) - Math.min(...starts)) / 1000));
}

export function filterPlans<T extends PlanLike>(
  plans: T[],
  keyword: string,
  status: string,
  type: string,
): T[] {
  const text = keyword.trim().toLowerCase();
  return plans.filter((plan) => {
    const matchesText = !text || plan.name.toLowerCase().includes(text);
    const matchesStatus = status === 'ALL' || plan.status === status;
    const matchesType = type === 'ALL' || plan.type === type;
    return matchesText && matchesStatus && matchesType;
  });
}

export function sortPlans<T extends PlanLike>(plans: T[], sortKey: string): T[] {
  const sorted = [...plans];
  if (sortKey === 'name_asc') return sorted.sort((a, b) => a.name.localeCompare(b.name));
  if (sortKey === 'status_asc') return sorted.sort((a, b) => a.status.localeCompare(b.status));
  if (sortKey === 'execute_asc') {
    return sorted.sort((a, b) => new Date(a.execute_time || a.created_at || 0).getTime() - new Date(b.execute_time || b.created_at || 0).getTime());
  }
  return sorted.sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime());
}

export function getWeekDay(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  return days[date.getDay()];
}

export function formatTriggerBy(trigger?: string | null): string {
  if (!trigger) return '未知';
  
  const val = trigger.trim();
  
  // 1. 系统调度映射
  if (val === 'scheduler_service') {
    return '系统自动';
  }
  
  // 2. 简易本地用户映射
  const userMapping: Record<string, string> = {
    'admin': '系统管理员',
    'unknown': '未知',
  };
  if (userMapping[val.toLowerCase()]) {
    return userMapping[val.toLowerCase()];
  }
  
  // 3. Jenkins 外部常见触发汉化
  if (val.startsWith('Started by user ')) {
    const username = val.replace('Started by user ', '');
    const mappedName = userMapping[username.toLowerCase()] || username;
    return `用户触发 (${mappedName})`;
  }
  if (val.startsWith('Started by timer')) {
    return '定时器触发';
  }
  if (val.startsWith('Started by upstream project')) {
    return '上游构建触发';
  }
  
  return val;
}

export function getQuickExecuteTime(
  currentValue: number | null,
  hour: number,
  minute: number,
  now = Date.now(),
): number {
  const value = new Date(currentValue ?? now);
  value.setHours(hour, minute, 0, 0);
  if (currentValue == null && value.getTime() <= now) value.setDate(value.getDate() + 1);
  return value.getTime();
}
