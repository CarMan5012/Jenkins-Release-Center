import pytest
import jwt
from unittest.mock import AsyncMock
from fastapi import WebSocketDisconnect
from app.api.ws import websocket_endpoint, verify_token
from app.core.config import settings
from app.core.ws_manager import manager

def create_valid_token() -> str:
    return jwt.encode({"sub": "admin"}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def test_verify_token():
    valid_token = create_valid_token()
    assert verify_token(valid_token) is True
    assert verify_token("invalid-token") is False
    assert verify_token(None) is False

@pytest.mark.asyncio
async def test_websocket_auth_success():
    token = create_valid_token()
    mock_ws = AsyncMock()
    
    # 在 send_json 被调用 AUTH_SUCCESS 时，断言 mock_ws 已经被加入 active_connections
    def check_added_to_manager(msg):
        if msg.get("type") == "AUTH_SUCCESS":
            assert mock_ws in manager.active_connections

    mock_ws.send_json.side_effect = check_added_to_manager
    mock_ws.receive_json.side_effect = [
        {"type": "AUTH", "token": token},
        {"type": "PING"},
        WebSocketDisconnect()
    ]

    await websocket_endpoint(mock_ws)

    mock_ws.accept.assert_called_once()
    assert mock_ws not in manager.active_connections  # 断开后自动被移除

@pytest.mark.asyncio
async def test_websocket_auth_invalid_token():
    mock_ws = AsyncMock()
    mock_ws.receive_json.return_value = {"type": "AUTH", "token": "invalid_token"}

    await websocket_endpoint(mock_ws)

    mock_ws.accept.assert_called_once()
    mock_ws.send_json.assert_called_once_with({"type": "AUTH_FAILED", "message": "Invalid token"})
    mock_ws.close.assert_called_once()
    assert mock_ws not in manager.active_connections

@pytest.mark.asyncio
async def test_websocket_auth_wrong_message_type():
    mock_ws = AsyncMock()
    mock_ws.receive_json.return_value = {"type": "PING"}

    await websocket_endpoint(mock_ws)

    mock_ws.accept.assert_called_once()
    mock_ws.send_json.assert_called_once_with({"type": "AUTH_FAILED", "message": "First message must be AUTH"})
    mock_ws.close.assert_called_once()
    assert mock_ws not in manager.active_connections

@pytest.mark.asyncio
async def test_broadcast_event_from_thread():
    import threading
    import asyncio

    mock_ws = AsyncMock()
    manager.add_connection(mock_ws)
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)

    def thread_broadcast():
        manager.broadcast_event("RELEASE_UPDATE", {"test": 123})

    t = threading.Thread(target=thread_broadcast)
    t.start()
    t.join()

    # 给主 loop 时间让 run_coroutine_threadsafe 的 task 运行
    await asyncio.sleep(0.1)
    mock_ws.send_json.assert_called_with({"type": "RELEASE_UPDATE", "data": {"test": 123}})
    manager.disconnect(mock_ws)
