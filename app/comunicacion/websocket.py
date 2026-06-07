"""
CU36 — WebSocket manager para actualizaciones en tiempo real.
Gestiona conexiones activas y permite broadcast a usuarios específicos.
"""
import json
import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect # type: ignore
from jose import JWTError # type: ignore

from app.core.security import decode_token

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, list[WebSocket]] = {} 

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(user_id, []).append(ws)

    def disconnect(self, user_id: int, ws: WebSocket) -> None:
        conns = self._connections.get(user_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, data: dict[str, Any]) -> None:
        conns = self._connections.get(user_id, [])
        dead: list[WebSocket] = []
        message = json.dumps(data, default=str)
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def broadcast_to_users(self, user_ids: list[int], data: dict[str, Any]) -> None:
        await asyncio.gather(
            *(self.send_to_user(uid, data) for uid in user_ids),
            return_exceptions=True,
        )

    @property
    def active_count(self) -> int:
        return sum(len(v) for v in self._connections.values())


manager = ConnectionManager()


async def notify(user_id: int, tipo: str, payload: dict[str, Any]) -> None:
    await manager.send_to_user(user_id, {"tipo": tipo, "payload": payload})


async def notify_many(user_ids: list[int], tipo: str, payload: dict[str, Any]) -> None:
    await manager.broadcast_to_users(user_ids, {"tipo": tipo, "payload": payload})


def _auth_from_token(token: str) -> tuple[int, str] | None:
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        role = payload.get("role", "")
        if not user_id:
            return None
        return int(user_id), role
    except (JWTError, ValueError):
        return None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.query_params.get("token")
    if not token:
        print("[WS] Rechazado: sin token")
        await ws.close(code=4001, reason="Token requerido")
        return

    auth = _auth_from_token(token)
    if not auth:
        print("[WS] Rechazado: token inválido")
        await ws.close(code=4001, reason="Token inválido")
        return

    user_id, role = auth
    await manager.connect(user_id, ws)
    print(f"[WS] Conectado user_id={user_id} role={role} (total: {manager.active_count})")
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("tipo") == "ping":
                    await ws.send_text(json.dumps({"tipo": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        print(f"[WS] Desconectado user_id={user_id} (clean)")
    except Exception as e:
        print(f"[WS] Error user_id={user_id}: {type(e).__name__}: {e}")
    finally:
        manager.disconnect(user_id, ws)
        print(f"[WS] Removido user_id={user_id} (total: {manager.active_count})")
