"""The PostTalk application facade."""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command

from bot.config import FIRST_PROMPT, SYSTEM_PROMPT
from bot.conversation import Conversations
from bot.generation import Generator
from bot.handlers import discussion, new
from bot.store import DialogStore, SqliteDialogStore, create_store

# An aiogram message handler: an async callable whose arguments are dependency-injected.
Handler = Callable[..., Awaitable[Any]]


class PostTalk:
    """Assembles the store, model and handlers into a bot you can run."""

    def __init__(
        self,
        *,
        generator: Generator,
        store: DialogStore | str,
        channels: list[int] | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        title_prompt: str = FIRST_PROMPT,
    ) -> None:
        self.generator = generator
        self.store: DialogStore = create_store(store) if isinstance(store, str) else store
        self.channels: list[int] = list(channels or [])
        self.system_prompt = system_prompt
        self.title_prompt = title_prompt
        self.router = Router(name="pt")
        self.conversations: Conversations | None = None

    def add_channel(self, channel_id: int) -> "PostTalk":
        """Register a channel the bot serves; returns self so calls can chain."""
        self.channels.append(channel_id)
        return self

    def setup(self, dp: Dispatcher) -> None:
        """Attach to an existing dispatcher: register handlers and inject services."""
        # Build the conversation engine now that the channels are configured. The title
        # target is the first channel for now; per-channel titling comes with the registry.
        if self.channels and self.conversations is None:
            self.conversations = Conversations(self.generator, self.store, self.channels[0])
        # /new in one of our channels opens a new conversation.
        self.router.channel_post.register(new, Command("new"), F.chat.id.in_(self.channels))
        # Answering comments needs the conversation engine; registered once it exists.
        if self.conversations is not None:
            self.router.message.register(discussion, F.chat.type == "supergroup")
        dp.include_router(self.router)
        dp["pt"] = self
        dp["store"] = self.store
        if self.conversations is not None:
            dp["conversations"] = self.conversations

    async def on_startup(self, bot: Bot) -> None:
        """Prepare resources before polling: connect a durable store.

        `bot` is unused for now; resolving each channel's linked discussion group
        (needed for /new from the group) will use it in a later step.
        """
        if isinstance(self.store, SqliteDialogStore):
            await self.store.connect()

    async def on_shutdown(self) -> None:
        """Release resources after polling: close a durable store."""
        if isinstance(self.store, SqliteDialogStore):
            await self.store.close()

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
        self.setup(dp)
        await self.on_startup(bot)
        try:
            await dp.start_polling(bot)
        finally:
            await self.on_shutdown()
