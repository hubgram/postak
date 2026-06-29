from bot.store.base import DEFAULT_WINDOW, Message, window_messages


class InMemoryDialogStore:
    """In-memory DialogStore (lost on restart). Handy for tests and dev."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = window
        self._pending: set[int] = set()
        self._dialogs: dict[int, list[Message]] = {}
        self._channel: dict[int, int] = {}

    async def mark_pending(self, channel_post_id: int) -> None:
        self._pending.add(channel_post_id)

    async def take_pending(self, channel_post_id: int) -> bool:
        if channel_post_id in self._pending:
            self._pending.discard(channel_post_id)
            return True
        return False

    async def start(self, thread_id: int, channel_post_id: int, system: str | None = None) -> None:
        self._dialogs[thread_id] = [{"role": "system", "content": system}] if system else []
        self._channel[thread_id] = channel_post_id

    async def channel_message(self, thread_id: int) -> int | None:
        return self._channel.get(thread_id)

    async def has(self, thread_id: int) -> bool:
        return thread_id in self._dialogs

    async def add(self, thread_id: int, role: str, content: str) -> None:
        self._dialogs[thread_id].append({"role": role, "content": content})

    async def history(self, thread_id: int) -> list[Message]:
        return window_messages(self._dialogs[thread_id], self._window)
