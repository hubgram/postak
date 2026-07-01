from typing import Protocol, TypeAlias

# A single chat message in OpenAI format: {"role": ..., "content": ...}.
Message: TypeAlias = dict[str, str]

# Identifies a thread or a pending post within one chat: (chat_id, thread_id) for
# dialogs, (channel_chat_id, channel_post_id) for pending posts. Telegram ids are
# only unique per chat, so the chat id is always part of the key.
Key: TypeAlias = tuple[int, int]

DEFAULT_WINDOW = 20


def window_messages(messages: list[Message], window: int) -> list[Message]:
    """Keep the leading system prompt (if any) plus the last `window` messages."""
    if messages and messages[0]["role"] == "system":
        return messages[:1] + messages[1:][-window:]
    return messages[-window:]


class DialogStore(Protocol):
    """Persists per-thread dialogs and the /new posts awaiting a thread, keyed by Key.

    Keying by (chat_id, id) lets the bot serve multiple channels/discussion groups at
    once. Any backend (in-memory, SQLite, Redis/Postgres later) is interchangeable.
    """

    async def mark_pending(self, key: Key) -> None:
        """Record a /new channel post that is awaiting its group forward."""
        ...

    async def take_pending(self, key: Key) -> bool:
        """Return True (and clear it) if this post was awaiting a forward."""
        ...

    async def start(self, key: Key, channel_post: Key, system: str | None = None) -> None:
        """Open a dialog for a thread, recording the channel post that opened it."""
        ...

    async def channel_message(self, key: Key) -> Key | None:
        """The (chat, message) key of the channel post that opened this thread, or None."""
        ...

    async def has(self, key: Key) -> bool:
        """Whether a dialog is open for the thread."""
        ...

    async def add(self, key: Key, role: str, content: str) -> None:
        """Append a message to the thread's dialog."""
        ...

    async def history(self, key: Key) -> list[Message]:
        """The windowed dialog: system prompt (if any) + most recent messages."""
        ...
