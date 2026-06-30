import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from openai import AsyncOpenAI

from bot.llm import (
    TitleSplitter,
    build_title_messages,
    completion_tokens,
    is_first_message,
    stream_answer,
)
from bot.rendering import stream_tokens
from bot.store import DialogStore

logger = logging.getLogger(__name__)


async def set_channel_title(bot: Bot, channel_id: int, message_id: int | None, title: str) -> None:
    if message_id is None or not title:
        return
    # Ignore edit failures: channel post deleted, not editable, or unchanged.
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_text(
            title, chat_id=channel_id, message_id=message_id, parse_mode=None
        )


@dataclass
class ThreadState:
    """A thread's queued comments and whether a generation is currently in flight."""

    pending: deque[Message] = field(default_factory=deque)
    generating: bool = False
    task: asyncio.Task[None] | None = None


class Conversations:
    """Answers each thread's comments via a per-thread batching queue.

    One background task per (chat, thread) drains and batches whatever is pending, so a
    thread never generates twice at once and the message handler never blocks on the LLM.
    Injected as a dependency instead of using module-level state.
    """

    def __init__(
        self,
        bot: Bot,
        client: AsyncOpenAI,
        model: str,
        store: DialogStore,
        target_channel_id: int,
    ) -> None:
        self._bot = bot
        self._client = client
        self._model = model
        self._store = store
        self._target_channel_id = target_channel_id
        self._states: dict[tuple[int, int], ThreadState] = {}

    def enqueue(self, message: Message, thread_id: int) -> None:
        key = (message.chat.id, thread_id)
        state = self._states.setdefault(key, ThreadState())
        state.pending.append(message)
        if not state.generating:
            state.generating = True
            state.task = asyncio.create_task(self._process(key))

    async def _process(self, key: tuple[int, int]) -> None:
        state = self._states[key]
        _, thread_id = key
        try:
            while state.pending:
                batch = list(state.pending)
                state.pending.clear()
                try:
                    await self._generate(batch, thread_id)
                except Exception:
                    logger.exception("failed to answer thread %s", key)
        finally:
            state.generating = False

    async def _generate(self, batch: list[Message], thread_id: int) -> None:
        """Store the batched user comments, generate one reply, and store it."""
        for msg in batch:
            if msg.text:
                await self._store.add(thread_id, "user", msg.text)
        reply_to = batch[-1]  # reply under the most recent comment
        history = await self._store.history(thread_id)
        if is_first_message(history):
            # First message: the LLM returns "title\nanswer". Stream only the answer
            # (title line hidden), title the channel post, and store the answer.
            splitter = TitleSplitter(
                completion_tokens(self._client, self._model, build_title_messages(history))
            )
            answer = await stream_tokens(reply_to, splitter.stream())
            channel_post_id = await self._store.channel_message(thread_id)
            if answer:
                await set_channel_title(
                    self._bot, self._target_channel_id, channel_post_id, splitter.title
                )
                await self._store.add(thread_id, "assistant", answer)
            else:
                # Model gave no answer after the title line; keep the line as the answer.
                await self._store.add(thread_id, "assistant", splitter.title)
        else:
            reply = await stream_answer(reply_to, self._client, self._model, history)
            await self._store.add(thread_id, "assistant", reply)
