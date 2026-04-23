from __future__ import annotations

import inspect
from typing import Any


def invoke_callback(callback, *args: Any) -> Any:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*args)

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if has_varargs:
        return callback(*args)
    if not positional:
        return callback()
    return callback(*args[: len(positional)])
