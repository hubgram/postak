"""The PostTalk application facade."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command

from bot.config import FIRST_PROMPT, SYSTEM_PROMPT
from bot.conversation import Conversations
from bot.handlers import discussion, new
from bot.store import DialogStore, create_store

# An aiogram message handler: an async callable whose arguments are dependency-injected.
Handler = Callable[..., Awaitable[Any]]


class PostTalk:
    """Assembles the store, model and handlers into a bot you can run."""

    def __init__(
        self,
        *,
        store: DialogStore | str,
        channels: list[int] | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        title_prompt: str = FIRST_PROMPT,
    ) -> None:
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

    def run(self, token: str) -> None:
        """Convenience entry point: build a Bot + Dispatcher and start polling."""
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2))
        dp = Dispatcher()
        self.setup(dp)
        dp.run_polling(bot)
