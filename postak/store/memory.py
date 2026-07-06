from postak.store.base import DEFAULT_WINDOW, AccessKey, Key, Message, window_messages


class InMemoryDialogStore:
    """In-memory DialogStore (lost on restart). Handy for tests and dev."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = window
        self._pending: set[Key] = set()
        self._dialogs: dict[Key, list[Message]] = {}
        self._channel: dict[Key, Key] = {}
        self._admins: set[int] = set()
        self._allowed: set[tuple[int, AccessKey]] = set()
        self._public: dict[AccessKey, bool] = {}

    async def mark_pending(self, key: Key) -> None:
        self._pending.add(key)

    async def take_pending(self, key: Key) -> bool:
        if key in self._pending:
            self._pending.discard(key)
            return True
        return False

    async def start(self, key: Key, channel_post: Key, system: str | None = None) -> None:
        self._dialogs[key] = [{"role": "system", "content": system}] if system else []
        self._channel[key] = channel_post

    async def channel_message(self, key: Key) -> Key | None:
        return self._channel.get(key)

    async def has(self, key: Key) -> bool:
        return key in self._dialogs

    async def add(self, key: Key, role: str, content: str) -> None:
        self._dialogs[key].append({"role": role, "content": content})

    async def add_many(self, key: Key, messages: list[Message]) -> None:
        self._dialogs[key].extend(messages)

    async def history(self, key: Key) -> list[Message]:
        return window_messages(self._dialogs[key], self._window)

    async def replace_history(self, key: Key, messages: list[Message]) -> None:
        self._dialogs[key] = list(messages)

    async def add_admin(self, user_id: int) -> None:
        self._admins.add(user_id)

    async def remove_admin(self, user_id: int) -> None:
        self._admins.discard(user_id)

    async def is_admin(self, user_id: int) -> bool:
        return user_id in self._admins

    async def admins(self) -> list[int]:
        return sorted(self._admins)

    async def allow_user(self, user_id: int, scope: AccessKey) -> None:
        self._allowed.add((user_id, scope))

    async def revoke_user(self, user_id: int, scope: AccessKey) -> None:
        self._allowed.discard((user_id, scope))

    async def is_user_allowed(self, user_id: int, scope: AccessKey) -> bool:
        return (user_id, scope) in self._allowed

    async def set_public(self, scope: AccessKey, public: bool) -> None:
        self._public[scope] = public

    async def get_public(self, scope: AccessKey) -> bool | None:
        return self._public.get(scope)

    async def allowed_users(self) -> list[tuple[int, AccessKey]]:
        return sorted(self._allowed, key=repr)

    async def public_scopes(self) -> list[tuple[AccessKey, bool]]:
        return list(self._public.items())
