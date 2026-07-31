from __future__ import annotations

import asyncio
import inspect
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from onestep.app import OneStepApp
from onestep.resilience import ConnectorOperationError

from .models import ConnectivityReport, ConnectivityResourceResult

_T = TypeVar("_T")


class _SyncLifecycleTimeout(asyncio.TimeoutError):
    pass


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


def _complete_sync_invocation(
    future: asyncio.Future[tuple[bool, Any]],
    outcome: tuple[bool, Any],
) -> None:
    if future.done():
        result = outcome[1]
        if outcome[0] and inspect.iscoroutine(result):
            result.close()
        return
    future.set_result(outcome)


async def _invoke_sync_lifecycle(
    method: Callable[[], _T],
    *,
    timeout_s: float,
    late_cleanup: Callable[[], Any] | None = None,
) -> _T:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[bool, Any]] = loop.create_future()
    decision_made = threading.Event()
    cleanup_required = threading.Event()

    def invoke() -> None:
        try:
            outcome = (True, method())
        except BaseException as exc:
            outcome = (False, exc)
        try:
            loop.call_soon_threadsafe(_complete_sync_invocation, future, outcome)
        except RuntimeError:
            result = outcome[1]
            if outcome[0] and inspect.iscoroutine(result):
                result.close()
        decision_made.wait()
        if cleanup_required.is_set() and late_cleanup is not None:
            _run_late_cleanup(late_cleanup)

    threading.Thread(
        target=invoke,
        daemon=True,
        name="onestep-connectivity-lifecycle",
    ).start()
    try:
        done, _ = await asyncio.wait((future,), timeout=timeout_s)
    except BaseException:
        cleanup_required.set()
        future.cancel()
        decision_made.set()
        raise
    if not done:
        cleanup_required.set()
        future.cancel()
        decision_made.set()
        raise _SyncLifecycleTimeout
    decision_made.set()
    succeeded, value = future.result()
    if not succeeded:
        raise value
    return value


def _run_late_cleanup(method: Callable[[], Any]) -> None:
    try:
        result = method()
        if inspect.isawaitable(result):
            asyncio.run(_await_late_cleanup(result))
    except BaseException:
        return


async def _await_late_cleanup(result: Any) -> None:
    await result


async def _invoke_lifecycle(
    method: Callable[[], Any],
    *,
    timeout_s: float,
    late_cleanup: Callable[[], Any] | None = None,
) -> None:
    started_at = time.monotonic()
    if inspect.iscoroutinefunction(method):
        result = method()
    else:
        result = await _invoke_sync_lifecycle(
            method,
            timeout_s=timeout_s,
            late_cleanup=late_cleanup,
        )
    if inspect.isawaitable(result):
        remaining_s = timeout_s - (time.monotonic() - started_at)
        if remaining_s <= 0:
            if inspect.iscoroutine(result):
                result.close()
            raise asyncio.TimeoutError
        await asyncio.wait_for(result, timeout=remaining_s)


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
    close_deferred = False
    try:
        await _invoke_lifecycle(
            opener,
            timeout_s=timeout_s,
            late_cleanup=closer,
        )
    except _SyncLifecycleTimeout:
        open_result = {"status": "timed_out"}
        close_deferred = True
    except asyncio.TimeoutError:
        open_result = {"status": "timed_out"}
    except Exception as exc:
        open_result = _error_result(exc)
    else:
        open_result = {"status": "connected"}

    if close_deferred:
        close_result = {"status": "timed_out"}
    else:
        try:
            await _invoke_lifecycle(closer, timeout_s=timeout_s)
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
