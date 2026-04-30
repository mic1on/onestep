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
