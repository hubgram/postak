import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from postak.generation import Generator
from postak.llm import TitleSplitter, build_title_messages, is_first_message
from postak.rendering import stream_tokens
from postak.store import DialogStore, Key

logger = logging.getLogger(__name__)


async def set_channel_title(bot: Bot | None, channel_post: Key | None, title: str) -> None:
    if bot is None or channel_post is None or not title:
        return
    channel_id, message_id = channel_post
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

    def __init__(self, generator: Generator, store: DialogStore) -> None:
        self._generator = generator
        self._store = store
        self._states: dict[Key, ThreadState] = {}

    def enqueue(self, message: Message, thread_id: int) -> None:
        key: Key = (message.chat.id, thread_id)
        state = self._states.setdefault(key, ThreadState())
        state.pending.append(message)
        if not state.generating:
            state.generating = True
            state.task = asyncio.create_task(self._process(key))

    async def drain(self, timeout: float = 30.0) -> None:
        """Let in-flight generations finish before shutdown, so replies aren't lost.

        Each running task drains its own pending queue, so awaiting the current
        tasks is enough. Stragglers past the timeout are cancelled rather than left
        to write into a store that is about to close.
        """
        tasks = [state.task for state in self._states.values() if state.task is not None]
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _process(self, key: Key) -> None:
        state = self._states[key]
        try:
            while state.pending:
                batch = list(state.pending)
                state.pending.clear()
                try:
                    await self._generate(batch, key)
                except Exception:
                    logger.exception("failed to answer thread %s", key)
                    await self._notify_failure(batch)
        finally:
            state.generating = False
            if not state.pending and self._states.get(key) is state:
                del self._states[key]

    async def _notify_failure(self, batch: list[Message]) -> None:
        """Tell the user a generation failed, so they aren't left with silence."""
        with contextlib.suppress(Exception):
            await batch[-1].reply(
                "\N{WARNING SIGN} I couldn't finish that reply. Please try again.",
                parse_mode=None,
            )

    async def _generate(self, batch: list[Message], key: Key) -> None:
        """Store the batched user comments, generate one reply, and store it."""
        for msg in batch:
            if msg.text:
                await self._store.add(key, "user", msg.text)
        reply_to = batch[-1]  # reply under the most recent comment
        history = await self._store.history(key)
        if is_first_message(history):
            # First message: the LLM returns "title\nanswer". Stream only the answer
            # (title line hidden), title the channel post, and store the answer.
            splitter = TitleSplitter(self._generator.tokens(build_title_messages(history)))
            answer = await stream_tokens(reply_to, splitter.stream())
            channel_post = await self._store.channel_message(key)
            # Title the channel post from the first line either way, so it never
            # stays "New Conversation". When there is no answer after the title
            # line, the line doubles as the reply.
            await set_channel_title(reply_to.bot, channel_post, splitter.title)
            await self._store.add(key, "assistant", answer or splitter.title)
        else:
            reply = await stream_tokens(reply_to, self._generator.tokens(history))
            await self._store.add(key, "assistant", reply)
