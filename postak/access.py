"""Scoped access control for deciding who Postak answers."""

from dataclasses import dataclass
from typing import Any, Literal

from aiogram.filters import BaseFilter
from aiogram.types import Message

from postak.store import AccessKey, AccessStore, DialogStore

AccessKind = Literal["global", "group", "thread"]


@dataclass(frozen=True)
class AccessScope:
    """A global, group, or thread scope for access rules."""

    kind: AccessKind
    chat_id: int | None = None
    thread_id: int | None = None

    @classmethod
    def global_(cls) -> "AccessScope":
        return cls("global")

    @classmethod
    def group(cls, chat_id: int) -> "AccessScope":
        return cls("group", chat_id=chat_id)

    @classmethod
    def thread(cls, chat_id: int, thread_id: int) -> "AccessScope":
        return cls("thread", chat_id=chat_id, thread_id=thread_id)

    def key(self) -> AccessKey:
        return self.kind, self.chat_id, self.thread_id


def make_scope(
    scope: AccessScope | str = "global",
    *,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> AccessScope:
    """Build an AccessScope from library/API arguments."""
    if isinstance(scope, AccessScope):
        return scope
    if scope == "global":
        return AccessScope.global_()
    if scope == "group":
        if chat_id is None:
            raise ValueError("group access scope requires chat_id")
        return AccessScope.group(chat_id)
    if scope == "thread":
        if chat_id is None or thread_id is None:
            raise ValueError("thread access scope requires chat_id and thread_id")
        return AccessScope.thread(chat_id, thread_id)
    raise ValueError(f"Unknown access scope: {scope!r}")


class AccessPolicy:
    """Evaluates additive access rules for Postak replies and admin commands."""

    def __init__(
        self,
        store: AccessStore,
        *,
        default_access: Literal["everyone", "restricted"] = "everyone",
    ) -> None:
        self._store = store
        self._default_public = default_access == "everyone"

    async def add_admin(self, user_id: int) -> None:
        await self._store.add_admin(user_id)

    async def remove_admin(self, user_id: int) -> None:
        await self._store.remove_admin(user_id)

    async def allow_user(self, user_id: int, scope: AccessScope) -> None:
        await self._store.allow_user(user_id, scope.key())

    async def revoke_user(self, user_id: int, scope: AccessScope) -> None:
        await self._store.revoke_user(user_id, scope.key())

    async def allow_everyone(self, scope: AccessScope) -> None:
        await self._store.set_public(scope.key(), True)

    async def restrict_everyone(self, scope: AccessScope) -> None:
        await self._store.set_public(scope.key(), False)

    async def admins(self) -> list[int]:
        return await self._store.admins()

    async def allowed_users(self) -> list[tuple[int, AccessKey]]:
        return await self._store.allowed_users()

    async def public_scopes(self) -> list[tuple[AccessKey, bool]]:
        return await self._store.public_scopes()

    async def clear_chat(self, chat_id: int) -> None:
        await self._store.clear_chat(chat_id)

    async def is_public(self, scope: AccessScope) -> bool:
        stored = await self._store.get_public(scope.key())
        if stored is not None:
            return stored
        return scope.kind == "global" and self._default_public

    async def can_manage(self, message: Message) -> bool:
        # An anonymous admin posts as the group itself; only group admins can.
        if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
            return True
        user = message.from_user
        return user is not None and await self._store.is_admin(user.id)

    async def can_use(self, message: Message, chat_id: int) -> bool:
        """Whether this user may act in a group outside any thread (e.g. /new)."""
        scopes = (AccessScope.global_(), AccessScope.group(chat_id))
        return await self._can_access(message, scopes)

    async def can_answer(self, message: Message, chat_id: int, thread_id: int) -> bool:
        scopes = (
            AccessScope.global_(),
            AccessScope.group(chat_id),
            AccessScope.thread(chat_id, thread_id),
        )
        return await self._can_access(message, scopes)

    async def _can_access(self, message: Message, scopes: tuple[AccessScope, ...]) -> bool:
        # Loops rather than any([await ...]) so each check short-circuits on the
        # first match instead of eagerly running every query.
        for scope in scopes:
            if await self.is_public(scope):
                return True

        user = message.from_user
        if user is None:
            return False
        if await self._store.is_admin(user.id):
            return True
        for scope in scopes:
            if await self._store.is_user_allowed(user.id, scope.key()):
                return True
        return False


class CanAnswer(BaseFilter):
    """Gate candidate discussion comments and inject their thread id."""

    def __init__(self, store: DialogStore, policy: AccessPolicy) -> None:
        self._store = store
        self._policy = policy

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        thread_id = message.message_thread_id
        if thread_id is None or not (message.text or message.caption):
            return False
        if not await self._store.has((message.chat.id, thread_id)):
            return False
        if not await self._policy.can_answer(message, message.chat.id, thread_id):
            return False
        return {"thread_id": thread_id}
