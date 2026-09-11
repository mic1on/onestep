from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from ._shared import (
    FeishuBitableApiError,
    FeishuBitablePayloadError,
    feishu_bitable_text,
    feishu_bitable_user,
)
from .connector import FeishuBitableConnector
from .resources import register_resources
from .sink import FeishuBitableTableSink
from .source import FeishuBitableIncrementalSource

try:
    __version__ = _package_version("onestep-feishu-bitable")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "FeishuBitableApiError",
    "FeishuBitableConnector",
    "FeishuBitableIncrementalSource",
    "FeishuBitablePayloadError",
    "FeishuBitableTableSink",
    "__version__",
    "feishu_bitable_text",
    "feishu_bitable_user",
    "register",
    "register_resources",
]
