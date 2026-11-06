"""Import-graph tests for the ``ops`` package (issue #216).

Before #216 the ``ops`` package sat in a module-level import cycle:

    ops/__init__ -> ops/readiness -> workers/__init__
        -> api/__init__ -> api.routers/health -> ops/readiness

which detonated as ``ImportError ... partially initialized`` whenever ``ops``
(or ``ops.observability``) was imported first. #216 breaks the cycle at its
``ops`` edges: the package ``__init__`` re-exports its six public names
lazily through a module-level ``__getattr__``, and ``ops.readiness`` defers
its workers imports to call time inside
``build_default_background_task_states``.

These tests pin the repaired graph. Each runs in a fresh subprocess so the
assertions see a pristine ``sys.modules`` regardless of what the rest of the
suite has already imported.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Modules whose presence in ``sys.modules`` after importing a given entry
# point would mean the cycle is back.
_WORKERS_MODULES = (
    "onestep_control_plane_api.workers",
    "onestep_control_plane_api.workers.notification_scanner",
    "onestep_control_plane_api.workers.notification_outbox_worker",
    "onestep_control_plane_api.workers.retention_worker",
)
_API_MODULES = (
    "onestep_control_plane_api.api",
    "onestep_control_plane_api.api.routers",
    "onestep_control_plane_api.api.notification_service",
)
_OPS_HEAVY_MODULES = (*_WORKERS_MODULES, *_API_MODULES, "onestep_control_plane_api.ops.readiness")


def _run_import_program(program: str) -> subprocess.CompletedProcess[str]:
    """Run ``program`` in a fresh interpreter with the package importable.

    Mirrors the subprocess isolation used in ``test_async_db_session.py``.
    """

    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env={**os.environ, "ONESTEP_CP_DATABASE_URL": "sqlite+pysqlite://"},
        check=False,
    )


def test_importing_ops_observability_does_not_pull_workers_or_api_routers() -> None:
    """The #216 acceptance check: ``import ops.observability`` stays leaf-level.

    ``ops.observability`` imports nothing from the project; importing it must
    not eagerly load ``ops.__init__``'s old re-export chain (readiness ->
    workers -> api) nor the ``api`` package at all.
    """

    program = (
        "import sys\n"
        "import onestep_control_plane_api.ops.observability\n"
        "banned = [\n"
        "    'onestep_control_plane_api.ops.readiness',\n"
        "    'onestep_control_plane_api.workers',\n"
        "    'onestep_control_plane_api.workers.notification_scanner',\n"
        "    'onestep_control_plane_api.workers.notification_outbox_worker',\n"
        "    'onestep_control_plane_api.workers.retention_worker',\n"
        "    'onestep_control_plane_api.api',\n"
        "    'onestep_control_plane_api.api.routers',\n"
        "    'onestep_control_plane_api.api.notification_service',\n"
        "]\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, f'import graph regression, loaded: {loaded}'\n"
        "print('clean')\n"
    )
    result = _run_import_program(program)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_importing_ops_first_does_not_trip_the_readiness_cycle() -> None:
    """``import ops`` (the old detonation path) must succeed and stay lazy.

    Before #216 this exact import order died with ``ImportError: cannot
    import name 'build_readiness_report' from partially initialized module
    'onestep_control_plane_api.ops.readiness'``. It must also not eagerly
    load ``readiness`` -- that is what keeps ``import ops`` free of the
    workers/api chain.
    """

    program = (
        "import sys\n"
        "import onestep_control_plane_api.ops\n"
        "assert 'onestep_control_plane_api.ops.readiness' not in sys.modules, "
        "'ops.__init__ eagerly imported readiness again'\n"
        "banned = [\n"
        "    'onestep_control_plane_api.workers.notification_scanner',\n"
        "    'onestep_control_plane_api.api.routers',\n"
        "]\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, f'import graph regression, loaded: {loaded}'\n"
        "print('clean')\n"
    )
    result = _run_import_program(program)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_importing_ops_readiness_does_not_pull_api_routers() -> None:
    """``ops.readiness`` must no longer import the workers eagerly.

    The workers imports live inside ``build_default_background_task_states``
    now; importing ``readiness`` alone must therefore leave the workers, the
    ``api`` package and the routers unloaded (health -> readiness closes the
    cycle through ``api.routers`` otherwise).
    """

    program = (
        "import sys\n"
        "import onestep_control_plane_api.ops.readiness\n"
        "banned = [\n"
        "    'onestep_control_plane_api.workers.notification_scanner',\n"
        "    'onestep_control_plane_api.workers.notification_outbox_worker',\n"
        "    'onestep_control_plane_api.workers.retention_worker',\n"
        "    'onestep_control_plane_api.api',\n"
        "    'onestep_control_plane_api.api.routers',\n"
        "]\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, f'import graph regression, loaded: {loaded}'\n"
        "print('clean')\n"
    )
    result = _run_import_program(program)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_ops_all_exports_resolve_through_the_lazy_getattr() -> None:
    """The six ``__all__`` names keep working through the lazy ``__getattr__``.

    Covers both ``from ops import X`` (the public usage) and
    ``ops.<name>`` attribute access, including the ``AttributeError`` path for
    names outside ``__all__``.
    """

    program = (
        "import onestep_control_plane_api.ops as ops\n"
        "from onestep_control_plane_api.ops import (\n"
        "    BackgroundTaskReadinessState,\n"
        "    RetentionRunReport,\n"
        "    RetentionTableReport,\n"
        "    build_default_background_task_states,\n"
        "    build_readiness_report,\n"
        "    run_retention,\n"
        ")\n"
        "for name in ops.__all__:\n"
        "    assert getattr(ops, name) is not None, name\n"
        "    assert name in dir(ops), name\n"
        "try:\n"
        "    ops.no_such_export\n"
        "except AttributeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('unknown names must raise AttributeError')\n"
        "states = build_default_background_task_states()\n"
        "assert set(states) == {\n"
        "    'notification_missed_start_scanner',\n"
        "    'notification_outbox_worker',\n"
        "    'retention_worker',\n"
        "}\n"
        "print('ok')\n"
    )
    result = _run_import_program(program)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_db_session_module_is_instrumented_at_import_time_without_the_gate() -> None:
    """The #215 import-time skip gate is retired (issue #216).

    ``db/session.py`` must no longer carry ``_MODULE_IMPORT_COMPLETE``: the
    factory instruments the import-time ``engine`` directly, because the ops
    import cycle that forced the gate is gone. The assertion runs in a
    subprocess where ``db.session`` is the *first* project module imported --
    the exact order that previously forced the gate to skip instrumentation.
    """

    program = (
        "import sys\n"
        "from onestep_control_plane_api.db import session as db_session\n"
        "assert not hasattr(db_session, '_MODULE_IMPORT_COMPLETE'), "
        "'import-time skip gate must be retired'\n"
        "assert db_session.ensure_sync_engine_instrumented() is True, "
        "'import-time engine must be instrumentable'\n"
        "import onestep_control_plane_api.ops.observability as obs\n"
        "samples = [\n"
        "    s for s in obs.collect_prometheus_snapshot().histograms\n"
        "    if s.name == 'onestep_control_plane_db_pool_wait_seconds'\n"
        "    and dict(s.labels)['name'] == db_session.SYNC_POOL_METRIC_NAME\n"
        "]\n"
        "assert samples, 'pool instrumentation series missing'\n"
        "print('clean')\n"
    )
    result = _run_import_program(program)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_full_app_import_still_works_from_every_entry_order() -> None:
    """Every documented entry order imports ``main`` without an ImportError.

    Guards against a future re-tightening of the graph: whichever module is
    imported first (ops, db.session, api), the full application package must
    initialize cleanly.
    """

    for first in (
        "import onestep_control_plane_api.ops",
        "import onestep_control_plane_api.db.session",
        "import onestep_control_plane_api.ops.observability",
        "import onestep_control_plane_api.main",
    ):
        program = f"{first}\nimport onestep_control_plane_api.main\nprint('ok')\n"
        result = _run_import_program(program)
        assert result.returncode == 0, f"{first!r}: {result.stderr}"
        assert "ok" in result.stdout
