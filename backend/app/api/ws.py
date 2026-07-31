import asyncio
import jwt
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from app.core.config import settings
from app.core.ws_manager import manager

logger = logging.getLogger(__name__)
router = APIRouter()

def verify_token(token: Optional[str]) -> bool:
    if not token:
        return False
    token_str = token.strip()
    if token_str.startswith("Bearer "):
        token_str = token_str[7:].strip()
    try:
        payload = jwt.decode(token_str, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        return username is not None
    except jwt.PyJWTError:
        return False

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # 确保保存主事件循环，保证线程安全事件广播
    try:
        manager.set_loop(asyncio.get_running_loop())
    except Exception:
        pass

    # 连接建立后，等待客户端 5 秒内发送首条 AUTH 消息进行身份认证
    try:
        auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        if isinstance(auth_data, dict) and auth_data.get("type") == "AUTH":
            token = auth_data.get("token")
            if verify_token(token):
                await websocket.send_json({"type": "AUTH_SUCCESS"})
            else:
                logger.warning("WebSocket 认证失败：Token 校验未通过")
                await websocket.send_json({"type": "AUTH_FAILED", "message": "Invalid token"})
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        else:
            logger.warning("WebSocket 认证失败：首条消息非 AUTH 认证消息")
            await websocket.send_json({"type": "AUTH_FAILED", "message": "First message must be AUTH"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except asyncio.TimeoutError:
        logger.warning("WebSocket 认证失败：超时未收到 AUTH 消息")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except Exception as e:
        logger.warning(f"WebSocket 认证过程发生异常: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 认证成功，加入在线广播池
    manager.add_connection(websocket)

    try:
        while True:
            data = await websocket.receive_json()
            # 响应客户端发起的 PING 保活心跳
            if isinstance(data, dict) and data.get("type") == "PING":
                await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        logger.info("WebSocket 客户端正常断开连接")
    except Exception as e:
        logger.warning(f"WebSocket 运行过程捕获异常: {e}")
    finally:
        manager.disconnect(websocket)

