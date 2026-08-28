"""Lifecycle, runner registry, and control-plane state for :class:`OneStepApp`.

The :class:`LifecycleController` owns the asyncio state of an app instance:
shutdown/drain/pause signals and waiters, the runner registry and per-task
asyncio handles, the ``serve()`` main loop, and the control-plane snapshots.
It holds a back-reference to the owning :class:`OneStepApp` so it can read the
task registry, events logger, named resources, and reporter summary without
exposing them as parameters. Public behaviour unchanged; invoked via facade
delegation on :class:`OneStepApp`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import signal
from collections.abc import Sequence
from typing import Any

from .runner import TaskRunner


class LifecycleController:
    """Owns the asyncio lifecycle of an :class:`OneStepApp` instance."""

    def __init__(self, app: Any) -> None:
        self._app = app
        # Signal/pause state
        self._shutdown: asyncio.Event | None = None
        self._shutdown_requested = False
        self._drain: asyncio.Event | None = None
        self._drain_requested = False
        self._restart_requested = False
        self._paused_tasks: set[str] = set()
        self._runner_state: asyncio.Event | None = None
        # Runner registry
        self._runners: list[TaskRunner] = []
        self._runner_tasks: dict[str, asyncio.Task[None]] = {}
        # Opened resources (set during startup, drained during shutdown).
        # Mirrors the original OneStepApp._resources attribute.
        self._resources: list[Any] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ----- state properties ------------------------------------------------

    @property
    def is_stopping(self) -> bool:
        return self._shutdown_requested or (self._shutdown.is_set() if self._shutdown is not None else False)

    @property
    def is_draining(self) -> bool:
        return self._drain_requested and not self.is_stopping

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    def is_task_paused(self, task_name: str) -> bool:
        return task_name in self._paused_tasks and not self.is_stopping

    # ----- request_* methods (control plane) -------------------------------

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._shutdown is None:
            return
        if self._loop is None or self._loop is current_loop:
            self._shutdown.set()
            return
        self._loop.call_soon_threadsafe(self._shutdown.set)

    def request_restart(self) -> None:
        self._restart_requested = True
        self.request_shutdown()

    def request_drain(self) -> None:
        self._drain_requested = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        drain = self._ensure_drain_event()
        if self._loop is None or self._loop is current_loop:
            drain.set()
        else:
            self._loop.call_soon_threadsafe(drain.set)
        self.notify_runner_state_changed()

    def request_task_pause(self, task_name: str) -> None:
        self._require_controllable_task(task_name)
        self._paused_tasks.add(task_name)
        self.notify_runner_state_changed()

    def request_task_resume(self, task_name: str) -> None:
        self._require_controllable_task(task_name)
        self._paused_tasks.discard(task_name)
        self.notify_runner_state_changed()

    def notify_runner_state_changed(self) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        runner_state = self._ensure_runner_state_event()
        if self._loop is None or self._loop is current_loop:
            runner_state.set()
            return
        self._loop.call_soon_threadsafe(runner_state.set)

    # ----- wait_* methods (control plane) ----------------------------------

    async def wait_for_shutdown(self) -> None:
        shutdown = self._ensure_shutdown_event()
        await shutdown.wait()

    async def wait_for_drain_request(self) -> None:
        drain = self._ensure_drain_event()
        await drain.wait()

    async def wait_for_task_pause_request(self, task_name: str) -> None:
        while not self.is_task_paused(task_name) and not self.is_stopping:
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_stop_fetching(self, task_name: str | None = None) -> None:
        shutdown_task = asyncio.create_task(self.wait_for_shutdown())
        drain_task = asyncio.create_task(self.wait_for_drain_request())
        waiters = {shutdown_task, drain_task}
        if task_name is not None:
            waiters.add(asyncio.create_task(self.wait_for_task_pause_request(task_name)))
        try:
            done, pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            raise
        for pending_task in pending:
            pending_task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)

    async def wait_for_drain(self) -> dict[str, Any]:
        while True:
            status = self.drain_status()
            if status["drained"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_task_pause(self, task_name: str) -> dict[str, Any]:
        while True:
            status = self.task_pause_status(task_name)
            if status["paused"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_task_resume(self, task_name: str) -> dict[str, Any]:
        while True:
            status = self.task_resume_status(task_name)
            if status["accepting_new_work"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    # ----- runner registry -------------------------------------------------

    def register_runners(self, runners: Sequence[TaskRunner]) -> None:
        self._runners = list(runners)
        self.notify_runner_state_changed()

    @property
    def runners(self) -> list[TaskRunner]:
        return self._runners

    @property
    def runner_tasks(self) -> dict[str, asyncio.Task[None]]:
        return self._runner_tasks

    @property
    def resources(self) -> list[Any]:
        return self._resources

    async def stop_task_runner(self, task_name: str) -> dict[str, Any]:
        """Tear down a single task's runner: cancel its coroutine (letting the
        runner's finally block drain inflight work and release fetch state),
        remove it from the registries, and close the task's *private* resources
        (source/sinks not referenced by any other live task or named resource).
        Other tasks and the process as a whole are not affected."""
        task = self._require_controllable_task(task_name)
        runner_handle = self._runner_tasks.pop(task_name, None)
        if runner_handle is not None and not runner_handle.done():
            runner_handle.cancel()
            await asyncio.gather(runner_handle, return_exceptions=True)
        self._runners = [runner for runner in self._runners if runner.task.name != task_name]
        self._paused_tasks.discard(task_name)

        # Close only resources private to this task; shared ones stay open.
        still_referenced = self._referenced_resource_ids(exclude_task_name=task_name)
        for resource in self._task_resources(task):
            if resource is None or id(resource) in still_referenced:
                continue
            with contextlib.suppress(Exception):
                self._app.events_logger.debug(
                    "closing private resource on task restart",
                    extra={"task_name": task_name, "resource": getattr(resource, "name", resource.__class__.__name__)},
                )
                await _close_resource(resource)

        self.notify_runner_state_changed()
        return self.task_control_snapshot(task_name)

    async def start_task_runner(self, task_name: str) -> dict[str, Any]:
        """Re-open the task's private source and spawn a fresh runner for it.
        Counterpart to stop_task_runner. Resources shared with other tasks are
        expected to already be open (they were never closed)."""
        task = self._require_controllable_task(task_name)
        assert task.source is not None
        # Re-open the task's resources. Shared resources already open are
        # idempotent to re-open for connectors whose open() is a no-op when
        # already connected; for safety, only open resources not currently
        # referenced by another live task (i.e. private ones we just closed).
        already_open_ids = self._referenced_resource_ids(exclude_task_name=task_name)
        for resource in self._task_resources(task):
            if resource is None or id(resource) in already_open_ids:
                continue
            await _open_resource(resource)

        runner = TaskRunner(self._app, task)
        self._runners.append(runner)
        runner_handle = asyncio.create_task(
            runner.run(), name=f"onestep-runner-{task_name}"
        )
        self._runner_tasks[task_name] = runner_handle
        self.notify_runner_state_changed()
        return self.task_control_snapshot(task_name)

    async def restart_task_runner(self, task_name: str) -> dict[str, Any]:
        """True per-task restart: cancel the existing runner, close and reopen
        its private source/sinks, and spawn a fresh runner coroutine. Other
        tasks keep running and the process is not restarted. Returns the task
        control snapshot for the restarted task."""
        await self.stop_task_runner(task_name)
        return await self.start_task_runner(task_name)

    def _task_resources(self, task: Any) -> list[Any]:
        """All resources owned by a task: its source, sinks, dead-letter sinks."""
        resources: list[Any] = []
        if task.source is not None:
            resources.append(task.source)
        resources.extend(task.sinks)
        resources.extend(task.dead_letter_sinks)
        return resources

    def _referenced_resource_ids(self, *, exclude_task_name: str | None) -> set[int]:
        """ids() of resources still referenced by other live tasks and named
        resources. Used to decide which resources are safe to close when
        restarting a single task: a resource shared with another task or a named
        resource must not be closed (mirrors startup()'s dedupe-by-id policy)."""
        referenced: set[int] = set()
        for name, resource in self._app._named_resources.items():
            if resource is not None:
                referenced.add(id(resource))
        if id(self._app.state) is not None:
            referenced.add(id(self._app.state))
        for task in self._app._tasks:
            if task.name == exclude_task_name:
                continue
            for resource in self._task_resources(task):
                referenced.add(id(resource))
        return referenced

    # ----- control-plane snapshots ----------------------------------------

    def drain_status(self) -> dict[str, Any]:
        inflight_task_count = sum(runner.inflight_count for runner in self._runners)
        fetching_runner_count = sum(1 for runner in self._runners if runner.is_fetching)
        parked_runner_count = sum(1 for runner in self._runners if runner.is_drain_parked)
        runner_count = len(self._runners)
        drained = (
            self._drain_requested
            and inflight_task_count == 0
            and fetching_runner_count == 0
            and parked_runner_count == runner_count
        )
        return {
            "operation": "drain",
            "requested": self._drain_requested,
            "completion": "complete" if drained else "in_progress",
            "drained": drained,
            "accepting_new_work": not self._drain_requested,
            "runner_count": runner_count,
            "parked_runner_count": parked_runner_count,
            "fetching_runner_count": fetching_runner_count,
            "inflight_task_count": inflight_task_count,
        }

    def task_pause_status(self, task_name: str) -> dict[str, Any]:
        status = self._task_runtime_status(task_name)
        paused = (
            status["pause_requested"]
            and status["inflight_task_count"] == 0
            and status["fetching_runner_count"] == 0
            and status["parked_runner_count"] == status["runner_count"]
        )
        return {
            "operation": "pause_task",
            "task_name": task_name,
            "requested": status["pause_requested"],
            "completion": "complete" if paused else "in_progress",
            "paused": paused,
            "accepting_new_work": not status["pause_requested"],
            "runner_count": status["runner_count"],
            "parked_runner_count": status["parked_runner_count"],
            "fetching_runner_count": status["fetching_runner_count"],
            "inflight_task_count": status["inflight_task_count"],
        }

    def task_control_snapshot(self, task_name: str) -> dict[str, Any]:
        task = next((task for task in self._app._tasks if task.name == task_name), None)
        if task is None:
            raise ValueError(f"task {task_name} was not found")
        if task.source is None:
            raise ValueError(f"task {task_name} does not have a controllable source runner")

        runners = [runner for runner in self._runners if runner.task.name == task_name]
        pause_requested = self.is_task_paused(task_name)
        fetching_runner_count = sum(1 for runner in runners if runner.is_fetching)
        parked_runner_count = sum(1 for runner in runners if runner.is_pause_parked)
        inflight_task_count = sum(runner.inflight_count for runner in runners)
        runner_count = len(runners)
        paused = (
            pause_requested
            and inflight_task_count == 0
            and fetching_runner_count == 0
            and parked_runner_count == runner_count
        )
        return {
            "task_name": task_name,
            "supported_commands": self.task_supported_commands(task_name),
            "pause_requested": pause_requested,
            "paused": paused,
            "accepting_new_work": not pause_requested,
            "runner_count": runner_count,
            "parked_runner_count": parked_runner_count,
            "fetching_runner_count": fetching_runner_count,
            "inflight_task_count": inflight_task_count,
        }

    def task_control_snapshots(self) -> list[dict[str, Any]]:
        return [
            self.task_control_snapshot(task.name)
            for task in self._app._tasks
            if task.source is not None
        ]

    def task_supported_commands(self, task_name: str) -> list[str]:
        task = self._require_controllable_task(task_name)
        supported_commands = ["pause_task", "resume_task", "restart_task"]
        if self._app._task_ops._task_supports_dead_letter_discard(task):
            supported_commands.append("discard_dead_letters")
        if self._app._task_ops._task_supports_dead_letter_replay(task):
            supported_commands.append("replay_dead_letters")
        if self._app._task_ops._task_supports_manual_run(task):
            supported_commands.append("run_task_once")
        return supported_commands

    def task_resume_status(self, task_name: str) -> dict[str, Any]:
        status = self._task_runtime_status(task_name)
        accepting_new_work = (
            not status["pause_requested"]
            and status["parked_runner_count"] == 0
        )
        return {
            "operation": "resume_task",
            "task_name": task_name,
            "requested": True,
            "completion": "complete" if accepting_new_work else "in_progress",
            "paused": not accepting_new_work,
            "accepting_new_work": accepting_new_work,
            "runner_count": status["runner_count"],
            "parked_runner_count": status["parked_runner_count"],
            "fetching_runner_count": status["fetching_runner_count"],
            "inflight_task_count": status["inflight_task_count"],
        }

    def _task_runtime_status(self, task_name: str) -> dict[str, Any]:
        runners = self._require_task_runners(task_name)
        return {
            "pause_requested": self.is_task_paused(task_name),
            "runner_count": len(runners),
            "parked_runner_count": sum(1 for runner in runners if runner.is_pause_parked),
            "fetching_runner_count": sum(1 for runner in runners if runner.is_fetching),
            "inflight_task_count": sum(runner.inflight_count for runner in runners),
        }

    # ----- lifecycle phases ------------------------------------------------

    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = False
        self._drain_requested = False
        self._restart_requested = False
        self._paused_tasks = set()
        self._shutdown = asyncio.Event()
        self._drain = asyncio.Event()
        self._runner_state = asyncio.Event()
        self._runners = []
        self._runner_tasks = {}
        resources: list[Any] = []
        seen: set[int] = set()

        def add_resource(resource: Any) -> None:
            if resource is None or id(resource) in seen:
                return
            resources.append(resource)
            seen.add(id(resource))

        for resource in self._app._named_resources.values():
            add_resource(resource)
        add_resource(self._app.state)
        for task in self._app._tasks:
            if task.source is not None:
                add_resource(task.source)
            for sink in task.sinks:
                add_resource(sink)
            for sink in task.dead_letter_sinks:
                add_resource(sink)
        opened: list[Any] = []
        self._resources = []
        try:
            for resource in resources:
                await _open_resource(resource)
                opened.append(resource)
            self._resources = list(opened)
            await self._app._events.run_hooks(self._app._events.startup_hooks)
        except Exception:
            await self._close_resources(opened, suppress_exceptions=True)
            self._resources = []
            raise

    async def shutdown(self) -> None:
        self._shutdown_requested = True
        if self._shutdown is not None:
            self._shutdown.set()
        hook_error: BaseException | None = None
        try:
            await self._app._events.run_hooks(self._app._events.shutdown_hooks)
        except BaseException as exc:
            hook_error = exc
        finally:
            close_error = await self._close_resources(self._resources, suppress_exceptions=False)
            self._resources = []
            for runner_task in self._runner_tasks.values():
                if not runner_task.done():
                    runner_task.cancel()
            if self._runner_tasks:
                await asyncio.gather(*self._runner_tasks.values(), return_exceptions=True)
            self._runner_tasks = {}
            self._runners = []
            self._paused_tasks = set()
            self.notify_runner_state_changed()
        if hook_error is not None:
            raise hook_error
        if close_error is not None:
            raise close_error

    async def serve(self) -> None:
        await self.startup()
        runners = [TaskRunner(self._app, task) for task in self._app._tasks if task.source is not None]
        self.register_runners(runners)
        try:
            if not runners:
                return
            # Spawn each runner as its own asyncio.Task and keep the handles in
            # _runner_tasks so they can be cancelled individually (per-task
            # restart via stop_task_runner). We wait with FIRST_COMPLETED and
            # loop, re-reading _runner_tasks each iteration so cancellations
            # and runners spawned by a per-task restart are picked up promptly.
            # A runner that ends with CancelledError because it was individually
            # cancelled for a restart must NOT bring down the whole process:
            # drop it and keep waiting. Any other exception is a real runner
            # error: cancel the remaining runners and propagate (matching the
            # previous asyncio.gather semantics).
            runner_tasks = [
                asyncio.create_task(runner.run(), name=f"onestep-runner-{runner.task.name}")
                for runner in runners
            ]
            self._runner_tasks = {runner.task.name: task for runner, task in zip(runners, runner_tasks)}
            inspected: set[asyncio.Task[None]] = set()
            while True:
                live = {task for task in self._runner_tasks.values() if task not in inspected}
                if not live:
                    if self.is_stopping:
                        break
                    try:
                        await asyncio.wait_for(self.wait_for_shutdown(), timeout=0.05)
                    except asyncio.TimeoutError:
                        pass
                    inspected = {task for task in inspected if task in self._runner_tasks.values()}
                    continue
                done, _pending = await asyncio.wait(live, return_when=asyncio.FIRST_COMPLETED)
                inspected |= done
                first_exc: BaseException | None = None
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is None:
                        continue
                    if isinstance(exc, asyncio.CancelledError):
                        continue
                    if first_exc is None:
                        first_exc = exc
                if first_exc is not None:
                    remaining = {task for task in self._runner_tasks.values() if task not in inspected}
                    for task in remaining:
                        task.cancel()
                    if remaining:
                        await asyncio.gather(*remaining, return_exceptions=True)
                    raise first_exc
            # All currently-tracked runners exited cleanly (normally via
            # request_shutdown or a per-task stop). Nothing left to await.
        finally:
            await self.shutdown()

    def run(self) -> None:
        try:
            with self._install_signal_handlers():
                asyncio.run(self.serve())
        except KeyboardInterrupt:
            return None

    # ----- internals -------------------------------------------------------

    async def _close_resources(
        self,
        resources: Sequence[Any],
        *,
        suppress_exceptions: bool,
    ) -> BaseException | None:
        first_error: BaseException | None = None
        for resource in reversed(resources):
            try:
                await _close_resource(resource)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                self._app.events_logger.exception(
                    "resource close failed",
                    extra={"resource_name": getattr(resource, "name", resource.__class__.__name__)},
                )
        if first_error is not None and not suppress_exceptions:
            return first_error
        return None

    def _require_controllable_task(self, task_name: str) -> Any:
        task = next((task for task in self._app._tasks if task.name == task_name), None)
        if task is None:
            raise ValueError(f"task {task_name} was not found")
        if task.source is None:
            raise ValueError(f"task {task_name} does not have a controllable source runner")
        return task

    def _require_task_runners(self, task_name: str) -> list[TaskRunner]:
        self._require_controllable_task(task_name)
        return [runner for runner in self._runners if runner.task.name == task_name]

    def _ensure_shutdown_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._shutdown is None or self._loop is not current_loop:
            self._loop = current_loop
            self._shutdown = asyncio.Event()
            if self._shutdown_requested:
                self._shutdown.set()
        return self._shutdown

    def _ensure_drain_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._drain is None or self._loop is not current_loop:
            self._loop = current_loop
            self._drain = asyncio.Event()
            if self._drain_requested:
                self._drain.set()
        return self._drain

    def _ensure_runner_state_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._runner_state is None or self._loop is not current_loop:
            self._loop = current_loop
            self._runner_state = asyncio.Event()
        return self._runner_state

    @contextlib.contextmanager
    def _install_signal_handlers(self):
        installed: list[tuple[int, Any]] = []

        def handle_signal(signum, frame) -> None:
            self.request_shutdown()

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)
                signal.signal(sig, handle_signal)
            except (ValueError, OSError):
                continue
            installed.append((sig, previous))
        try:
            yield
        finally:
            for sig, previous in reversed(installed):
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, previous)


# ---- module-level helpers ------------------------------------------------

async def _open_resource(resource: Any) -> None:
    opener = getattr(resource, "open", None)
    if not callable(opener):
        return
    result = opener()
    if inspect.isawaitable(result):
        await result


async def _close_resource(resource: Any) -> None:
    closer = getattr(resource, "close", None)
    if not callable(closer):
        return
    result = closer()
    if inspect.isawaitable(result):
        await result
