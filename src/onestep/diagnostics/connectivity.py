from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from onestep.app import OneStepApp
from onestep.resilience import ConnectorOperationError

from .models import ConnectivityReport, ConnectivityResourceResult


@dataclass
class _InventoryEntry:
    resource: Any
    aliases: list[str]
    roles: list[str]


def inventory_resources(app: OneStepApp) -> tuple[_InventoryEntry, ...]:
    entries: dict[int, _InventoryEntry] = {}

    def add(resource: Any, alias: str, role: str) -> None:
        if resource is None:
            return
        entry = entries.setdefault(id(resource), _InventoryEntry(resource, [], []))
        if alias not in entry.aliases:
            entry.aliases.append(alias)
        if role not in entry.roles:
            entry.roles.append(role)

    for name, resource in app.resources.items():
        add(resource, name, "named")
    add(app.state, "app.state", "state_store")
    for task in app.tasks:
        add(task.source, f"{task.name}.source", "source")
        for index, sink in enumerate(task.sinks):
            add(sink, f"{task.name}.emit[{index}]", "sink")
        for index, sink in enumerate(task.dead_letter_sinks):
            add(
                sink,
                f"{task.name}.dead_letter[{index}]",
                "dead_letter_sink",
            )
    return tuple(entries.values())


async def _invoke_lifecycle(method: Callable[[], Any]) -> None:
    result = method()
    if inspect.isawaitable(result):
        await result


def _error_result(exc: BaseException) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "failed",
        "exception_type": type(exc).__name__,
    }
    if isinstance(exc, ConnectorOperationError):
        result.update(
            {
                "backend": exc.backend,
                "operation": exc.operation.value,
                "kind": exc.kind.value,
            }
        )
    return result


async def _probe_entry(
    entry: _InventoryEntry,
    *,
    timeout_s: float,
) -> ConnectivityResourceResult:
    resource = entry.resource
    type_name = f"{type(resource).__module__}.{type(resource).__qualname__}"
    opener = getattr(resource, "open", None)
    closer = getattr(resource, "close", None)
    if not callable(opener) or not callable(closer):
        return ConnectivityResourceResult(
            aliases=tuple(entry.aliases),
            roles=tuple(entry.roles),
            type_name=type_name,
            probe_kind="none",
            status="not_probeable",
        )

    open_result: dict[str, Any]
    close_result: dict[str, Any]
    try:
        await asyncio.wait_for(_invoke_lifecycle(opener), timeout=timeout_s)
    except asyncio.TimeoutError:
        open_result = {"status": "timed_out"}
    except Exception as exc:
        open_result = _error_result(exc)
    else:
        open_result = {"status": "connected"}

    try:
        await asyncio.wait_for(_invoke_lifecycle(closer), timeout=timeout_s)
    except asyncio.TimeoutError:
        close_result = {"status": "timed_out"}
    except Exception as exc:
        close_result = _error_result(exc)
    else:
        close_result = {"status": "closed"}

    status = (
        "connected"
        if open_result["status"] == "connected"
        and close_result["status"] == "closed"
        else "failed"
    )
    return ConnectivityResourceResult(
        aliases=tuple(entry.aliases),
        roles=tuple(entry.roles),
        type_name=type_name,
        probe_kind="lifecycle",
        status=status,
        open=open_result,
        close=close_result,
    )


async def check_connectivity(
    app: OneStepApp,
    *,
    timeout_s: float,
) -> ConnectivityReport:
    if timeout_s <= 0:
        raise ValueError("connect timeout must be > 0")
    results = []
    for entry in inventory_resources(app):
        results.append(await _probe_entry(entry, timeout_s=timeout_s))
    lifecycle_results = [item for item in results if item.probe_kind == "lifecycle"]
    warnings = ()
    if not lifecycle_results:
        warnings = ("no connection was verified; all resources are not_probeable",)
    return ConnectivityReport(
        app=app.name,
        resources=tuple(results),
        ok=all(item.status == "connected" for item in lifecycle_results),
        warnings=warnings,
    )


__all__ = ["check_connectivity", "inventory_resources"]
