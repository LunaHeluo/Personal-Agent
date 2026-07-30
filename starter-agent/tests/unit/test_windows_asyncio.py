from starter_agent.mcp.windows_asyncio import (
    install_windows_proactor_reset_filter,
)


class _ProactorCloseHandle:
    def __repr__(self) -> str:
        return "<Handle _ProactorBasePipeTransport._call_connection_lost(None)>"


class _Loop:
    def __init__(self, previous=None):
        self.handler = previous
        self.defaults = []

    def get_exception_handler(self):
        return self.handler

    def set_exception_handler(self, handler):
        self.handler = handler

    def default_exception_handler(self, context):
        self.defaults.append(context)


def test_windows_proactor_filter_swallows_only_close_reset_10054() -> None:
    delegated = []

    def previous(loop, context):
        delegated.append(context)

    loop = _Loop(previous)
    restore = install_windows_proactor_reset_filter(
        loop,  # type: ignore[arg-type]
        platform="win32",
    )
    handler = loop.handler
    assert handler is not None

    reset = ConnectionResetError(10054, "remote host closed")
    handler(
        loop,
        {
            "message": "Exception in callback",
            "exception": reset,
            "handle": _ProactorCloseHandle(),
        },
    )
    handler(
        loop,
        {
            "message": "unrelated callback failed",
            "exception": RuntimeError("real failure"),
        },
    )

    assert delegated == [
        {
            "message": "unrelated callback failed",
            "exception": delegated[0]["exception"],
        }
    ]
    restore()
    assert loop.handler is previous


def test_proactor_filter_delegates_on_non_windows_and_without_prior_handler() -> None:
    loop = _Loop()
    restore = install_windows_proactor_reset_filter(
        loop,  # type: ignore[arg-type]
        platform="linux",
    )

    assert loop.handler is None
    restore()
    assert loop.defaults == []
