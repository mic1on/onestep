from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class LiveAgentConnection:
    instance_id: UUID
    session_id: str
    send_queue: asyncio.Queue[dict[str, object]]


class AgentConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[UUID, LiveAgentConnection] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        instance_id: UUID,
        session_id: str,
        send_queue: asyncio.Queue[dict[str, object]],
    ) -> LiveAgentConnection:
        connection = LiveAgentConnection(
            instance_id=instance_id,
            session_id=session_id,
            send_queue=send_queue,
        )
        async with self._lock:
            self._connections[instance_id] = connection
        return connection

    async def unregister(self, *, instance_id: UUID, session_id: str) -> None:
        async with self._lock:
            current = self._connections.get(instance_id)
            if current is not None and current.session_id == session_id:
                self._connections.pop(instance_id, None)

    async def get(self, instance_id: UUID) -> LiveAgentConnection | None:
        async with self._lock:
            return self._connections.get(instance_id)


agent_connection_registry = AgentConnectionRegistry()
