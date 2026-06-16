from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class LiveWorkerAgentConnection:
    worker_agent_id: UUID
    session_id: str
    send_queue: asyncio.Queue[dict[str, object]]


class WorkerAgentConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[UUID, LiveWorkerAgentConnection] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        worker_agent_id: UUID,
        session_id: str,
        send_queue: asyncio.Queue[dict[str, object]],
    ) -> LiveWorkerAgentConnection:
        connection = LiveWorkerAgentConnection(
            worker_agent_id=worker_agent_id,
            session_id=session_id,
            send_queue=send_queue,
        )
        async with self._lock:
            self._connections[worker_agent_id] = connection
        return connection

    async def unregister(self, *, worker_agent_id: UUID, session_id: str) -> None:
        async with self._lock:
            current = self._connections.get(worker_agent_id)
            if current is not None and current.session_id == session_id:
                self._connections.pop(worker_agent_id, None)

    async def get(self, worker_agent_id: UUID) -> LiveWorkerAgentConnection | None:
        async with self._lock:
            return self._connections.get(worker_agent_id)


worker_agent_connection_registry = WorkerAgentConnectionRegistry()
