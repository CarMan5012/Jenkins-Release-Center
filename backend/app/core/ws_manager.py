import asyncio
import logging
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        if loop and loop.is_running():
            self.loop = loop
            logger.info("ConnectionManager 主事件循环已成功绑定")

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.add_connection(websocket)

    def add_connection(self, websocket: WebSocket):
        if websocket not in self.active_connections:
            self.active_connections.append(websocket)
            logger.info(f"WebSocket 客户端已加入在线连接池，当前在线数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket 客户端已断开，当前在线数: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """异步广播 JSON 消息给所有在线客户端"""
        if not self.active_connections:
            return

        disconnected = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"发送 WebSocket 消息失败: {e}")
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    def broadcast_event(self, event_type: str, data: Any = None):
        """线程安全地向所有客户端广播指定事件"""
        message = {"type": event_type, "data": data}
        
        target_loop = None
        try:
            target_loop = asyncio.get_running_loop()
        except RuntimeError:
            target_loop = self.loop

        if target_loop and target_loop.is_running():
            try:
                curr_loop = asyncio.get_running_loop()
                if curr_loop is target_loop:
                    asyncio.create_task(self.broadcast(message))
                else:
                    asyncio.run_coroutine_threadsafe(self.broadcast(message), target_loop)
            except RuntimeError:
                asyncio.run_coroutine_threadsafe(self.broadcast(message), target_loop)
            logger.debug(f"已向 {len(self.active_connections)} 个客户端发起 WebSocket 事件广播 [{event_type}]")
        else:
            logger.warning(f"无法广播 WebSocket 事件 [{event_type}]: 未找到运行中的 asyncio 事件循环 (在线客户端数: {len(self.active_connections)})")

manager = ConnectionManager()

