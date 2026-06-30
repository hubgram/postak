import asyncio
import contextlib

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


# One asyncio.Lock per (chat, thread) so a thread's comments are answered one at a
# time; different threads still run concurrently. thread_id is only unique per chat.
_thread_locks: dict[tuple[int, int], asyncio.Lock] = {}


def thread_lock(chat_id: int, thread_id: int) -> asyncio.Lock:
    return _thread_locks.setdefault((chat_id, thread_id), asyncio.Lock())


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

    # Serialize per thread: never generate two answers for the same thread at once;
    # concurrent comments queue and are answered one by one.
    async with thread_lock(message.chat.id, thread_id):
        await store.add(thread_id, "user", message.text)
        history = await store.history(thread_id)
        if is_first_message(history):
            # First message: the LLM returns "title\nanswer". Stream only the answer
            # (title line hidden), title the channel post, and store the answer.
            splitter = TitleSplitter(
                completion_tokens(client, model, build_title_messages(history))
            )
            answer = await stream_tokens(message, splitter.stream())
            channel_post_id = await store.channel_message(thread_id)
            if answer:
                await set_channel_title(bot, target_channel_id, channel_post_id, splitter.title)
                await store.add(thread_id, "assistant", answer)
            else:
                # Model gave no answer after the title line; keep the line as the answer.
                await store.add(thread_id, "assistant", splitter.title)
        else:
            reply = await stream_answer(message, client, model, history)
            await store.add(thread_id, "assistant", reply)
