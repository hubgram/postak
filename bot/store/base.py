from typing import Protocol

# A single chat message in OpenAI format: {"role": ..., "content": ...}.
Message = dict[str, str]

DEFAULT_WINDOW = 20


def window_messages(messages: list[Message], window: int) -> list[Message]:
    """Keep the leading system prompt (if any) plus the last `window` messages."""
    if messages and messages[0]["role"] == "system":
        return messages[:1] + messages[1:][-window:]
    return messages[-window:]


class DialogStore(Protocol):
    """Persists per-thread dialogs and the /new posts awaiting a discussion thread.

    Any backend (in-memory, SQLite, and later Redis/Postgres) that implements
    these async methods is interchangeable; handlers depend only on this.
    """

    async def mark_pending(self, channel_post_id: int) -> None:
        """Record a /new channel post that is awaiting its group forward."""
        ...

    async def take_pending(self, channel_post_id: int) -> bool:
        """Return True (and clear it) if this post was awaiting a forward."""
        ...

    async def start(self, thread_id: int, channel_post_id: int, system: str | None = None) -> None:
        """Open a dialog for a thread, recording its channel post id and optional system."""
        ...

    async def channel_message(self, thread_id: int) -> int | None:
        """The channel post id that opened this thread (to edit its title), or None."""
        ...

    async def has(self, thread_id: int) -> bool:
        """Whether a dialog is open for the thread."""
        ...

    async def add(self, thread_id: int, role: str, content: str) -> None:
        """Append a message to the thread's dialog."""
        ...

    async def history(self, thread_id: int) -> list[Message]:
        """The windowed dialog: system prompt (if any) + most recent messages."""
        ...
