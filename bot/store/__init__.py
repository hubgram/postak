from bot.store.base import DEFAULT_WINDOW, DialogStore, Message, window_messages
from bot.store.memory import InMemoryDialogStore
from bot.store.sqlite import SqliteDialogStore

__all__ = [
    "DEFAULT_WINDOW",
    "DialogStore",
    "InMemoryDialogStore",
    "Message",
    "SqliteDialogStore",
    "create_store",
    "window_messages",
]

_SQLITE_SCHEMES = ("sqlite+aiosqlite://", "sqlite://")


def create_store(url: str, window: int = DEFAULT_WINDOW) -> DialogStore:
    """Build a DialogStore from a database URL."""
    if url in (":memory:", "memory"):
        return InMemoryDialogStore(window=window)
    for scheme in _SQLITE_SCHEMES:
        if url.startswith(scheme):
            path = url[len(scheme) :].removeprefix("/") or ":memory:"
            return SqliteDialogStore(path, window=window)
    raise ValueError(f"Unsupported database URL: {url!r}")
