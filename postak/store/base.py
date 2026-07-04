from typing import Protocol, TypeAlias

# A single chat message in OpenAI format: {"role": ..., "content": ...}.
Message: TypeAlias = dict[str, str]

# Identifies a thread or a pending post within one chat: (chat_id, thread_id) for
# dialogs, (channel_chat_id, channel_post_id) for pending posts. Telegram ids are
# only unique per chat, so the chat id is always part of the key.
Key: TypeAlias = tuple[int, int]

# Access scopes are stored as (kind, chat_id, thread_id). Global scopes have no
# chat/thread, group scopes have a chat, and thread scopes have both.
AccessKey: TypeAlias = tuple[str, int | None, int | None]

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

    async def replace_history(self, key: Key, messages: list[Message]) -> None:
        """Replace the stored dialog history for a thread."""
        ...


class AccessStore(Protocol):
    """Persists admins and scoped access rules."""

    async def add_admin(self, user_id: int) -> None:
        """Grant Postak admin rights to a Telegram user."""
        ...

    async def remove_admin(self, user_id: int) -> None:
        """Remove Postak admin rights from a Telegram user."""
        ...

    async def is_admin(self, user_id: int) -> bool:
        """Whether this Telegram user is a Postak admin."""
        ...

    async def allow_user(self, user_id: int, scope: AccessKey) -> None:
        """Allow a Telegram user in a specific access scope."""
        ...

    async def revoke_user(self, user_id: int, scope: AccessKey) -> None:
        """Remove a Telegram user's explicit access in a scope."""
        ...

    async def is_user_allowed(self, user_id: int, scope: AccessKey) -> bool:
        """Whether this Telegram user is explicitly allowed in a scope."""
        ...

    async def set_public(self, scope: AccessKey, public: bool) -> None:
        """Set whether everyone may use Postak in a scope."""
        ...

    async def get_public(self, scope: AccessKey) -> bool | None:
        """Return a stored public flag, or None when the scope uses defaults."""
        ...


class Store(DialogStore, AccessStore, Protocol):
    """Combined storage contract used by Postak."""

    pass
