from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any


def install_windows_proactor_reset_filter(
    loop: Any,
    *,
    platform: str | None = None,
) -> Callable[[], None]:
    """Filter the benign Windows Proactor reset raised during pipe teardown."""

    if (platform or sys.platform) != "win32":
        return lambda: None
    previous = loop.get_exception_handler()

    def handler(active_loop: Any, context: dict[str, Any]) -> None:
        if not _is_benign_proactor_close_reset(context):
            if previous is not None:
                previous(active_loop, context)
            else:
                active_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)

    def restore() -> None:
        if loop.get_exception_handler() is handler:
            loop.set_exception_handler(previous)

    return restore


def _is_benign_proactor_close_reset(context: dict[str, Any]) -> bool:
    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError):
        return False
    code = getattr(exception, "winerror", None) or getattr(
        exception, "errno", None
    )
    if code != 10054:
        return False
    callback = f"{context.get('message', '')} {context.get('handle', '')}"
    return "_ProactorBasePipeTransport._call_connection_lost" in callback
