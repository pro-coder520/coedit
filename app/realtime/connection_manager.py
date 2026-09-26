import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._ready: set[WebSocket] = set()
        self._pending: dict[WebSocket, list[str]] = {}

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._rooms.setdefault(room_id, set()).add(websocket)
            self._pending[websocket] = []

    async def activate(self, websocket: WebSocket) -> None:
        async with self._lock:
            for message in self._pending.pop(websocket, []):
                await websocket.send_text(message)
            self._ready.add(websocket)

    async def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._remove_connection(room_id, websocket)

    async def broadcast(
        self,
        room_id: str,
        message: str,
        sender: WebSocket | None = None,
    ) -> None:
        async with self._lock:
            recipients = tuple(
                websocket
                for websocket in self._rooms.get(room_id, ())
                if websocket is not sender and websocket in self._ready
            )
            for websocket in self._rooms.get(room_id, ()):
                if websocket is not sender and websocket not in self._ready:
                    self._pending[websocket].append(message)

        results = await asyncio.gather(
            *(websocket.send_text(message) for websocket in recipients),
            return_exceptions=True,
        )
        failed = [
            websocket
            for websocket, result in zip(recipients, results)
            if isinstance(result, Exception)
        ]

        if failed:
            async with self._lock:
                for websocket in failed:
                    self._remove_connection(room_id, websocket)

    def _remove_connection(self, room_id: str, websocket: WebSocket) -> None:
        self._ready.discard(websocket)
        self._pending.pop(websocket, None)
        connections = self._rooms.get(room_id)
        if connections is None:
            return

        connections.discard(websocket)
        if not connections:
            del self._rooms[room_id]
