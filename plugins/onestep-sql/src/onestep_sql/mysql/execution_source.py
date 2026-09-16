"""MySQL tracked execution source and delivery (thin backend subclasses).

Phase 3 of ``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
(§7.2–§7.4, §13) binds the MySQL side of the source/delivery pair that Phase 1
moved into :mod:`onestep_sql._shared.execution.source`. These two classes keep
the backend-named public surface (design §4.2: never a generic
``SQLExecutionSource``) and bind only the two genuinely MySQL-specific seams:

* the source name prefix ``mysql.execution:<namespace>``;
* :func:`onestep_sql.mysql.resilience.as_mysql_connector_operation_error`,
  which owns the MySQL error-classification table.

Everything else — option validation, the claim loop, envelope construction
with ``onestep.execution`` correlation metadata, the heartbeat loop,
cooperative cancellation and the ``ack``/``retry``/``fail`` mapping — is
inherited unchanged from the shared base. The source constructor parameter set
is identical to :class:`~onestep_sql.postgres.execution_source.PostgresExecutionSource`
(design §10.1), so business code can switch backends without touching it.

``_validate_execution_source_options`` is re-exported because
``onestep_sql.mysql.resources`` imports it from this module, mirroring the
published import shape of ``onestep_sql.postgres.execution_source`` (design
§7.4 keeps the per-backend module shapes aligned).
"""

from __future__ import annotations

from typing import Any

# ``_validate_execution_source_options`` is re-exported for
# ``onestep_sql.mysql.resources`` exactly the way the PostgreSQL module does it,
# so both resource modules share one import shape (design §7.4).
from .._shared.execution.source import (  # noqa: F401
    ExecutionDeliveryBase,
    ExecutionSourceBase,
    _validate_execution_source_options,
)
from .execution_backend import MySQLExecutionBackend
from .resilience import as_mysql_connector_operation_error

# Compatibility re-exports: kept so this module's attribute surface mirrors
# ``onestep_sql.postgres.execution_source`` one-to-one; reviewers can diff the
# two backend modules and see only the dialect seams (design §7.2/§7.3).
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


class MySQLExecutionDelivery(ExecutionDeliveryBase["MySQLExecutionSource"]):
    """MySQL managed-execution delivery (name and signature frozen)."""

    def __init__(
        self,
        *,
        source: MySQLExecutionSource,
        lease: ExecutionLease,
        envelope: Envelope,
    ) -> None:
        super().__init__(source=source, lease=lease, envelope=envelope)


class MySQLExecutionSource(ExecutionSourceBase):
    _source_kind = "mysql.execution"
    _backend_cls = MySQLExecutionBackend
    _delivery_cls = MySQLExecutionDelivery
    _error_factory = staticmethod(as_mysql_connector_operation_error)

    @classmethod
    def from_connector(cls, connector: Any, **kwargs: Any) -> "MySQLExecutionSource":
        return super().from_connector(connector, **kwargs)


__all__ = [
    "MySQLExecutionDelivery",
    "MySQLExecutionSource",
]
