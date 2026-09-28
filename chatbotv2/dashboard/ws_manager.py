import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

logger = logging.getLogger("ws_manager")


@dataclass
class ManagedConnection:
    websocket: WebSocket
    dialog_ids: set[int] = field(default_factory=set)
    is_global: bool = False
    creator_ids: set[int] = field(default_factory=set)


class ConnectionManager:
    """Manages WebSocket connections with scoped event delivery.

    Never holds the lock while awaiting WebSocket network I/O.
    """

    def __init__(self) -> None:
        self._connections: list[ManagedConnection] = []
        self._lock = asyncio.Lock()

    async def connect(
        self, websocket: WebSocket, *, dialog_ids: set[int] | None = None, is_global: bool = False, creator_ids: set[int] | None = None
    ) -> ManagedConnection:
        await websocket.accept()
        conn = ManagedConnection(
            websocket=websocket, dialog_ids=dialog_ids or set(), is_global=is_global, creator_ids=creator_ids or set()
        )
        async with self._lock:
            self._connections.append(conn)
        logger.debug("WebSocket connected (dialog_ids=%s, global=%s, creator_ids=%s)", dialog_ids, is_global, creator_ids)
        return conn

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections = [c for c in self._connections if c.websocket is not websocket]
        logger.debug("WebSocket disconnected")

    async def broadcast(self, event: dict, *, dialog_id: int | None = None, creator_id: int | None = None) -> None:
        """Send an event to all matching connections.

        If dialog_id is provided, deliver to connections subscribed to that dialog
        AND to global connections. If dialog_id is None, deliver to global connections only.
        Creator scoping: if event has creator_id, only deliver to connections that explicitly
        include that creator_id. A connection with an empty creator filter (e.g. creator
        resolution failed at connect time) receives only creator-less global events, never
        creator-scoped ones. Server-side isolation, not frontend.
        """
        async with self._lock:
            targets = []
            for conn in self._connections:
                # Creator scoping: creator-scoped events go only to matching creator connections.
                # M6: an empty creator filter must NOT match all creators (fail closed).
                # The dashboard route always passes an explicit set; an empty set means
                # "unresolved", and such connections keep only truly global events.
                # Polling remains the fallback for missed user-scoped updates.
                if creator_id is not None and creator_id not in conn.creator_ids:
                    continue
                # Dialog/global scoping
                if dialog_id is not None and dialog_id in conn.dialog_ids:
                    targets.append(conn)
                elif conn.is_global:
                    targets.append(conn)
                elif creator_id is not None and conn.creator_ids and creator_id in conn.creator_ids:
                    targets.append(conn)

            if not targets:
                return

            message = json.dumps(event)
            stale: list[WebSocket] = []

        # Send without holding the lock
        for conn in targets:
            try:
                await conn.websocket.send_text(message)
            except Exception:  # noqa: BLE001
                stale.append(conn.websocket)

        # Clean up failed connections
        if stale:
            async with self._lock:
                self._connections = [c for c in self._connections if c.websocket not in stale]

    @property
    def connection_count(self) -> int:
        return len(self._connections)


_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
