from postak.store.base import DEFAULT_WINDOW, DialogStore, Key, Message, window_messages
from postak.store.memory import InMemoryDialogStore
from postak.store.sqlite import SqliteDialogStore

__all__ = [
    "DEFAULT_WINDOW",
    "DialogStore",
    "InMemoryDialogStore",
    "Key",
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
