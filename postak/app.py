"""The Postak application facade."""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from postak.access import AccessPolicy, AccessScope, CanAnswer, make_scope
from postak.config import FIRST_PROMPT, SYSTEM_PROMPT
from postak.conversation import Conversations
from postak.generation import Generator
from postak.handlers import answer_discussion, new, new_from_group, open_discussion, postak_admin
from postak.store import SqliteDialogStore, Store, create_store

# An aiogram message handler: an async callable whose arguments are dependency-injected.
Handler = Callable[..., Awaitable[Any]]


class FromDiscussion(BaseFilter):
    """Passes messages in a known discussion group, injecting its channel id.

    Returning a dict is aiogram's way for a filter to add values to the handler's
    arguments, so `new_from_group` receives `target_channel_id` without a lookup.
    """

    def __init__(self, channel_of_group: dict[int, int]) -> None:
        self._channel_of_group = channel_of_group

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        channel_id = self._channel_of_group.get(message.chat.id)
        if channel_id is None:
            return False
        return {"target_channel_id": channel_id}


class Postak:
    """Assembles the store, model and handlers into a bot you can run."""

    def __init__(
        self,
        *,
        generator: Generator,
        store: Store | str,
        channels: list[int] | None = None,
        admins: list[int] | None = None,
        default_access: Literal["everyone", "restricted"] = "everyone",
        system_prompt: str = SYSTEM_PROMPT,
        title_prompt: str = FIRST_PROMPT,
    ) -> None:
        self.generator = generator
        self.store: Store = create_store(store) if isinstance(store, str) else store
        self.channels: list[int] = list(channels or [])
        self.system_prompt = system_prompt
        self.title_prompt = title_prompt
        self.router = Router(name="pt")
        self.conversations = Conversations(self.generator, self.store)
        self.access_policy = AccessPolicy(self.store, default_access=default_access)
        self._initial_admins: set[int] = set(admins or [])
        self._initial_admin_removals: set[int] = set()
        self._initial_allowed: set[tuple[int, AccessScope]] = set()
        self._initial_revoked: set[tuple[int, AccessScope]] = set()
        self._initial_public: dict[AccessScope, bool] = {}
        # discussion-group chat id -> its channel id, filled in on_startup.
        self.discussion_channel: dict[int, int] = {}

    def add_channel(self, channel_id: int) -> "Postak":
        """Register a channel the bot serves; returns self so calls can chain."""
        self.channels.append(channel_id)
        return self

    def add_admin(self, user_id: int) -> "Postak":
        """Grant Postak admin rights during startup configuration."""
        self._initial_admins.add(user_id)
        self._initial_admin_removals.discard(user_id)
        return self

    def remove_admin(self, user_id: int) -> "Postak":
        """Remove Postak admin rights during startup configuration."""
        self._initial_admins.discard(user_id)
        self._initial_admin_removals.add(user_id)
        return self

    def allow_user(
        self,
        user_id: int,
        scope: AccessScope | str = "global",
        *,
        chat_id: int | None = None,
        thread_id: int | None = None,
    ) -> "Postak":
        """Allow a Telegram user in a global, group, or thread scope."""
        access_scope = make_scope(scope, chat_id=chat_id, thread_id=thread_id)
        item = (user_id, access_scope)
        self._initial_allowed.add(item)
        self._initial_revoked.discard(item)
        return self

    def revoke_user(
        self,
        user_id: int,
        scope: AccessScope | str = "global",
        *,
        chat_id: int | None = None,
        thread_id: int | None = None,
    ) -> "Postak":
        """Revoke a Telegram user's explicit access during startup configuration."""
        access_scope = make_scope(scope, chat_id=chat_id, thread_id=thread_id)
        item = (user_id, access_scope)
        self._initial_allowed.discard(item)
        self._initial_revoked.add(item)
        return self

    def allow_everyone(
        self,
        scope: AccessScope | str = "global",
        *,
        chat_id: int | None = None,
        thread_id: int | None = None,
    ) -> "Postak":
        """Make a global, group, or thread scope public during startup configuration."""
        self._initial_public[make_scope(scope, chat_id=chat_id, thread_id=thread_id)] = True
        return self

    def restrict_everyone(
        self,
        scope: AccessScope | str = "global",
        *,
        chat_id: int | None = None,
        thread_id: int | None = None,
    ) -> "Postak":
        """Make a global, group, or thread scope non-public during startup configuration."""
        self._initial_public[make_scope(scope, chat_id=chat_id, thread_id=thread_id)] = False
        return self

    def attach(self, dp: Dispatcher) -> None:
        """Attach to an existing dispatcher: register handlers and inject services."""
        # /new in one of our channels opens a new conversation.
        self.router.channel_post.register(new, Command("new"), F.chat.id.in_(self.channels))
        # /new by an admin in a linked discussion group opens one in that channel.
        self.router.message.register(
            new_from_group, Command("new"), FromDiscussion(self.discussion_channel)
        )
        # Postak admins can manage admins and access rules.
        self.router.message.register(postak_admin, Command("postak"))
        # Automatic forwards open dialogs; comments are gated separately before generation.
        self.router.message.register(
            open_discussion, F.chat.type == "supergroup", F.is_automatic_forward
        )
        self.router.message.register(
            answer_discussion,
            F.chat.type == "supergroup",
            CanAnswer(self.store, self.access_policy),
        )
        dp.include_router(self.router)
        dp["pt"] = self
        dp["store"] = self.store
        dp["conversations"] = self.conversations
        dp["access_policy"] = self.access_policy
        dp.startup.register(self._startup)
        dp.shutdown.register(self._shutdown)

    async def on_startup(self, bot: Bot) -> None:
        """Connect a durable store and map each channel to its linked discussion group,
        so admins can run /new from the group."""
        if isinstance(self.store, SqliteDialogStore):
            await self.store.connect()
        await self._apply_initial_access()
        for channel_id in self.channels:
            discussion_id = (await bot.get_chat(channel_id)).linked_chat_id
            if discussion_id is not None:
                self.discussion_channel[discussion_id] = channel_id

    async def on_shutdown(self) -> None:
        """Release resources after polling: close a durable store."""
        if isinstance(self.store, SqliteDialogStore):
            await self.store.close()

    async def _startup(self, bot: Bot) -> None:
        await self.on_startup(bot)

    async def _shutdown(self) -> None:
        await self.on_shutdown()

    def run(self, token: str) -> None:
        """Convenience entry point: build a Bot + Dispatcher, start up, poll, shut down."""
        coro = self._run(token)
        try:
            import uvloop

        except ImportError:
            return asyncio.run(coro)

        else:
            if sys.version_info >= (3, 11):
                with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
                    return runner.run(coro)
            else:  # pragma: no cover
                uvloop.install()
                return asyncio.run(coro)

    async def _run(self, token: str) -> None:
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2))
        dp = Dispatcher()
        self.attach(dp)
        await dp.start_polling(bot)

    async def _apply_initial_access(self) -> None:
        for user_id in self._initial_admins:
            await self.access_policy.add_admin(user_id)
        for user_id in self._initial_admin_removals:
            await self.access_policy.remove_admin(user_id)
        for user_id, scope in self._initial_allowed:
            await self.access_policy.allow_user(user_id, scope)
        for user_id, scope in self._initial_revoked:
            await self.access_policy.revoke_user(user_id, scope)
        for scope, public in self._initial_public.items():
            if public:
                await self.access_policy.allow_everyone(scope)
            else:
                await self.access_policy.restrict_everyone(scope)
