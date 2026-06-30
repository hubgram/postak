from bot.store.base import DEFAULT_WINDOW, Key, Message, window_messages


class InMemoryDialogStore:
    """In-memory DialogStore (lost on restart). Handy for tests and dev."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = window
        self._pending: set[Key] = set()
        self._dialogs: dict[Key, list[Message]] = {}
        self._channel: dict[Key, int] = {}

    async def mark_pending(self, key: Key) -> None:
        self._pending.add(key)

    async def take_pending(self, key: Key) -> bool:
        if key in self._pending:
            self._pending.discard(key)
            return True
        return False

    async def start(self, key: Key, channel_post_id: int, system: str | None = None) -> None:
        self._dialogs[key] = [{"role": "system", "content": system}] if system else []
        self._channel[key] = channel_post_id

    async def channel_message(self, key: Key) -> int | None:
        return self._channel.get(key)

    async def has(self, key: Key) -> bool:
        return key in self._dialogs

    async def add(self, key: Key, role: str, content: str) -> None:
        self._dialogs[key].append({"role": role, "content": content})

    async def history(self, key: Key) -> list[Message]:
        return window_messages(self._dialogs[key], self._window)
