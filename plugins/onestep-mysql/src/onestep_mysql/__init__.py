# Forwarding shim (issue #133 / design PR #134).
# onestep-mysql now delegates to the canonical onestep-sql package so the
# public API (class/function/exception identities) is preserved as a drop-in
# replacement while the MySQL+PostgreSQL consolidation lands.
from onestep_sql.mysql import *  # noqa: F401,F403

__version__ = "0.6.1"
