DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_LOOKBACK_MINUTES = 15
MAX_LOOKBACK_MINUTES = 24 * 60
DEFAULT_RECENT_EVENT_LIMIT = 10
MAX_RECENT_EVENT_LIMIT = 50
DEFAULT_TASK_ACTIVITY_LIMIT = 20
MAX_TASK_ACTIVITY_LIMIT = 100
HEALTH_STATUS_VALUES = ("ok", "degraded", "error", "starting", "unknown")
TASK_EVENT_KIND_VALUES = (
    "started",
    "failed",
    "retried",
    "dead_lettered",
    "cancelled",
    "succeeded",
)
# Event kinds (TaskEvent.kind) that mark a task as failing. Used by the service
# level failing_task_count. Note: timeouts live on TaskMetricWindow, not
# TaskEvent, so they are folded into error_count at the task summary level.
TASK_FAILING_EVENT_KINDS = ("failed", "dead_lettered")
# Config keys inspected (in order) to derive a human-readable source/sink label
# from connector config dicts.
SOURCE_LABEL_CONFIG_KEYS = ("topic", "queue", "stream", "url", "schedule", "cron")
SINK_LABEL_CONFIG_KEYS = ("topic", "queue", "stream", "url", "table", "database")
# Config keys inspected (in order) to derive retry attempts from retry policy.
RETRY_ATTEMPTS_CONFIG_KEYS = ("attempts", "max_attempts", "retries")
