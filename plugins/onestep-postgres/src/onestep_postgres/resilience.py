# Forwarding shim (issue #133 / design PR #134).
# onestep-postgres now delegates to the canonical onestep-sql package so the
# public AND private API is preserved as a drop-in replacement while the
# MySQL+PostgreSQL consolidation lands.
import sys
from onestep_sql.postgres import resilience as _canonical_module
sys.modules[__name__] = _canonical_module
