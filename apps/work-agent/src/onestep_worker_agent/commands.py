"""Durable command outcomes: a transport reconnect never repeats an operation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from weakref import WeakValueDictionary


class CommandJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _record_path(self, command_id: str) -> Path:
        return self.path / (hashlib.sha256(command_id.encode()).hexdigest() + ".json")

    def load(self, command_id: str) -> dict | None:
        path = self._record_path(command_id)
        return json.loads(path.read_text()) if path.exists() else None

    def save(self, record: dict) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        path = self._record_path(record["command_id"])
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name != "nt":
            directory = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def lock(self, deployment_id: str) -> asyncio.Lock:
        return self.locks.setdefault(deployment_id, asyncio.Lock())

    def begin(self, command_id: str, payload: dict) -> dict:
        record = {
            "command_id": command_id,
            "payload": payload,
            "runtime_instance_id": str(uuid4()),
            "frames": {},
        }
        self.save(record)
        return record


class JournalSocket:
    """Persist terminal outcomes before attempting transport delivery.

    Sending on a broken session is best effort. The plane requests unfinished
    commands again after hello; the journal then returns the original outcome.
    """

    def __init__(self, websocket, journal: CommandJournal, record: dict) -> None:
        self.websocket = websocket
        self.journal = journal
        self.record = record

    async def send(self, text: str) -> None:
        frame = json.loads(text)
        if frame["type"] in {"command_ack", "command_result"}:
            self.record["frames"][frame["type"]] = frame
            self.journal.save(self.record)
        try:
            await self.websocket.send(text)
        except Exception:
            # Network failure must not turn a completed start into a failed
            # deployment or delete its only process handle.
            pass

    async def replay(self) -> None:
        for frame in self.record["frames"].values():
            await self.send(json.dumps(frame))
