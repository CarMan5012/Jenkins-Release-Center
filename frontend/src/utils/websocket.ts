import { useAuthStore } from '../store/auth';

type EventCallback = (data: any) => void;

class WebSocketService {
  private socket: WebSocket | null = null;
  private listeners: Map<string, Set<EventCallback>> = new Map();
  private pingTimer: any = null;
  private reconnectTimer: any = null;
  private reconnectAttempts = 0;
  private isExplicitlyClosed = false;

  public connect() {
    this.isExplicitlyClosed = false;
    const authStore = useAuthStore();
    const token = authStore.token;

    if (!token) {
      console.warn('[WS] 无法建立连接：未获取到 Token');
      return;
    }

    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 根据 location 自动拼装基础路径（不再在 URL 中附加 token 参数）
    const wsUrl = `${protocol}//${location.host}/jenkins/api/v1/ws`;

    try {
      this.socket = new WebSocket(wsUrl);

      this.socket.onopen = () => {
        console.log('[WS] WebSocket 连接建立，正在发送 AUTH 认证消息...');
        // 连接建立后立即发送首条 AUTH 消息
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
          this.socket.send(JSON.stringify({ type: 'AUTH', token }));
        }
      };

      this.socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'AUTH_SUCCESS') {
            console.log('[WS] WebSocket 身份认证成功');
            this.reconnectAttempts = 0;
            this.startPing();
            this.emit('_connected', null);
            // 重新建立连接后，主动向所有订阅者广播刷新事件以补偿断连期间可能遗漏的状态
            this.emit('RELEASE_UPDATE', null);
            this.emit('HISTORY_UPDATE', null);
            return;
          }
          if (message.type === 'AUTH_FAILED') {
            console.error('[WS] WebSocket 身份认证失败:', message.message);
            this.isExplicitlyClosed = true; // 阻止 onclose 触发重连
            this.socket?.close();
            return;
          }
          if (message.type === 'PONG') {
            return;
          }
          if (message.type) {
            this.emit(message.type, message.data);
          }
        } catch (e) {
          console.error('[WS] 消息解析失败:', e);
        }
      };

      this.socket.onerror = (error) => {
        console.error('[WS] WebSocket 连接错误:', error);
      };

      this.socket.onclose = (event) => {
        this.stopPing();
        this.emit('_disconnected', event);
        // 如果是 1008 (Policy Violation/认证失败)，放弃重连防止重连风暴
        if (event.code === 1008) {
          console.warn('[WS] WebSocket 因认证失败被服务端断开(1008)，终止重连');
          this.isExplicitlyClosed = true;
          return;
        }
        if (!this.isExplicitlyClosed) {
          this.scheduleReconnect();
        }
      };
    } catch (err) {
      console.error('[WS] 创建 WebSocket 失败:', err);
      this.scheduleReconnect();
    }
  }

  public disconnect() {
    this.isExplicitlyClosed = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.listeners.clear();
    console.log('[WS] WebSocket 手动断开连接并清空监听池');
  }

  public on(eventType: string, callback: EventCallback) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType)!.add(callback);
  }

  public off(eventType: string, callback: EventCallback) {
    const callbacks = this.listeners.get(eventType);
    if (callbacks) {
      callbacks.delete(callback);
    }
  }

  private emit(eventType: string, data: any) {
    const callbacks = this.listeners.get(eventType);
    if (callbacks) {
      callbacks.forEach((cb) => {
        try {
          cb(data);
        } catch (e) {
          console.error(`[WS] 执行事件回调错误 (${eventType}):`, e);
        }
      });
    }
  }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: 'PING' }));
      }
    }, 25000);
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.isExplicitlyClosed) return;
    this.reconnectAttempts++;
    // 指数退避算法: 1s, 2s, 4s, 最长 10s
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 10000);
    console.log(`[WS] 即将在 ${delay}ms 后尝试第 ${this.reconnectAttempts} 次重连...`);

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }
}

export const wsService = new WebSocketService();
