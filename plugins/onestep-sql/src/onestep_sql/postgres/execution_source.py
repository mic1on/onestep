"""PostgreSQL tracked execution source and delivery (thin backend subclasses).

Phase 1 of ``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
(§7.2–§7.4) moved the source/delivery lifecycle into
:mod:`onestep_sql._shared.execution.source`. These two classes keep the public
names, module path and constructor signatures the compatibility window
requires, and bind only the two genuinely PostgreSQL-specific seams:

* the source name prefix ``postgres.execution:<namespace>``;
* :func:`onestep_sql.postgres.resilience.as_postgres_connector_operation_error`,
  which owns the PostgreSQL error-classification table.

``_validate_execution_source_options`` is re-exported because
``onestep_sql.postgres.resources`` (and its published import path) imports it
from this module.
"""

from __future__ import annotations

from typing import Any

# ``_validate_execution_source_options`` used to be defined in this module and is
# imported from here by ``onestep_sql.postgres.resources``; keep re-exporting it
# so that intra-package import path does not change (design §7.4).
from .._shared.execution.source import (  # noqa: F401
    ExecutionDeliveryBase,
    ExecutionSourceBase,
    _validate_execution_source_options,
)
from .execution_backend import PostgresExecutionBackend
from .resilience import as_postgres_connector_operation_error

# Compatibility re-exports: these names were reachable as attributes of this
# module before the Phase 1 extraction (it imported them for the source/delivery
# pair that has since moved out). The published surface is ``__all__`` and is
# unchanged; re-exporting keeps incidental attribute access working, as Phase 1's
# zero-behaviour-change gate requires.
from onestep.connectors.base import Delivery, Source  # noqa: F401
from onestep.envelope import Envelope  # noqa: F401
from onestep.execution import (  # noqa: F401
    ExecutionCompletion,
    ExecutionErrorDetail,
    ExecutionLease,
    ExecutionStatus,
    HeartbeatResult,
    LeasedExecutionBackend,
)


class PostgresExecutionDelivery(ExecutionDeliveryBase):
    """PostgreSQL managed-execution delivery (name and signature frozen)."""


class PostgresExecutionSource(ExecutionSourceBase):
    _source_kind = "postgres.execution"
    _backend_cls = PostgresExecutionBackend
    _delivery_cls = PostgresExecutionDelivery
    _error_factory = staticmethod(as_postgres_connector_operation_error)

    @classmethod
    def from_connector(cls, connector: Any, **kwargs: Any) -> "PostgresExecutionSource":
        return super().from_connector(connector, **kwargs)


__all__ = [
    "PostgresExecutionDelivery",
    "PostgresExecutionSource",
]
