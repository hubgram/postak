import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, MessageOriginChannel
from openai import AsyncOpenAI

from bot.config import NEW_MESSAGE, SYSTEM_PROMPT
from bot.llm import (
    TitleSplitter,
    build_title_messages,
    completion_tokens,
    is_first_message,
    stream_answer,
)
from bot.rendering import stream_tokens
from bot.store import DialogStore


async def start_conversation(bot: Bot, channel_id: int, store: DialogStore) -> None:
    """Post the new-conversation message to the channel; its auto-forward opens a thread."""
    sent = await bot.send_message(channel_id, NEW_MESSAGE)
    await store.mark_pending(sent.message_id)


async def new(message: Message, bot: Bot, store: DialogStore) -> None:
    # /new posted in the channel itself.
    await start_conversation(bot, message.chat.id, store)


async def is_chat_admin(bot: Bot, message: Message) -> bool:
    # An anonymous admin posts as the group itself; only admins can do that.
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return True
    user = message.from_user
    if user is None:
        return False
    member = await bot.get_chat_member(message.chat.id, user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


async def new_from_group(
    message: Message, bot: Bot, store: DialogStore, target_channel_id: int
) -> None:
    # An admin (named or anonymous) runs /new in the discussion group -> start it in the channel.
    if await is_chat_admin(bot, message):
        await start_conversation(bot, target_channel_id, store)


def forwarded_channel_post_id(message: Message) -> int | None:
    """Original channel post id behind an automatic forward, or None."""
    if isinstance(origin := message.forward_origin, MessageOriginChannel):
        return origin.message_id
    return message.forward_from_message_id


async def set_channel_title(bot: Bot, channel_id: int, message_id: int | None, title: str) -> None:
    if message_id is None or not title:
        return
    # Ignore edit failures: channel post deleted, not editable, or unchanged.
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_text(
            title, chat_id=channel_id, message_id=message_id, parse_mode=None
        )


logger = logging.getLogger(__name__)


@dataclass
class ThreadState:
    """A thread's queued comments and whether a generation is currently in flight."""

    pending: deque[Message] = field(default_factory=deque)
    generating: bool = False
    task: asyncio.Task[None] | None = None


# Per (chat, thread) queue. Comments are appended and answered by a single background
# task that batches whatever is pending, so a thread never generates twice at once and
# the message handler returns immediately instead of blocking on the LLM.
_thread_states: dict[tuple[int, int], ThreadState] = {}


async def generate_reply(
    batch: list[Message],
    thread_id: int,
    bot: Bot,
    client: AsyncOpenAI,
    model: str,
    store: DialogStore,
    target_channel_id: int,
) -> None:
    """Store the batched user comments, generate one reply, and store it."""
    for msg in batch:
        if msg.text:
            await store.add(thread_id, "user", msg.text)
    reply_to = batch[-1]  # reply under the most recent comment
    history = await store.history(thread_id)
    if is_first_message(history):
        # First message: the LLM returns "title\nanswer". Stream only the answer
        # (title line hidden), title the channel post, and store the answer.
        splitter = TitleSplitter(completion_tokens(client, model, build_title_messages(history)))
        answer = await stream_tokens(reply_to, splitter.stream())
        channel_post_id = await store.channel_message(thread_id)
        if answer:
            await set_channel_title(bot, target_channel_id, channel_post_id, splitter.title)
            await store.add(thread_id, "assistant", answer)
        else:
            # Model gave no answer after the title line; keep the line as the answer.
            await store.add(thread_id, "assistant", splitter.title)
    else:
        reply = await stream_answer(reply_to, client, model, history)
        await store.add(thread_id, "assistant", reply)


async def process_thread(
    key: tuple[int, int],
    bot: Bot,
    client: AsyncOpenAI,
    model: str,
    store: DialogStore,
    target_channel_id: int,
) -> None:
    """Drain a thread's queue, answering one batch at a time until it is empty."""
    state = _thread_states[key]
    _, thread_id = key
    try:
        while state.pending:
            batch = list(state.pending)
            state.pending.clear()
            try:
                await generate_reply(batch, thread_id, bot, client, model, store, target_channel_id)
            except Exception:
                logger.exception("failed to answer thread %s", key)
    finally:
        state.generating = False


async def discussion(
    message: Message,
    bot: Bot,
    client: AsyncOpenAI,
    model: str,
    store: DialogStore,
    target_channel_id: int,
) -> None:
    # A channel post is auto-forwarded into the discussion group as the root of
    # its comment thread. If it came from a /new post, open a dialog for it.
    if message.is_automatic_forward:
        channel_post_id = forwarded_channel_post_id(message)
        if channel_post_id is not None and await store.take_pending(channel_post_id):
            await store.start(message.message_id, channel_post_id, system=SYSTEM_PROMPT)
        return

    thread_id = message.message_thread_id
    if thread_id is None or not message.text or not await store.has(thread_id):
        return

    # Queue the comment; a single background task answers the thread's comments in
    # batches, so we never generate two replies for one thread at the same time.
    key = (message.chat.id, thread_id)
    state = _thread_states.setdefault(key, ThreadState())
    state.pending.append(message)
    if not state.generating:
        state.generating = True
        state.task = asyncio.create_task(
            process_thread(key, bot, client, model, store, target_channel_id)
        )
