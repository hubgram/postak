from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, MessageOriginChannel

from bot.config import NEW_MESSAGE, SYSTEM_PROMPT
from bot.conversation import Conversations
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


async def discussion(message: Message, store: DialogStore, conversations: Conversations) -> None:
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

    # Hand the comment to the per-thread batching worker.
    conversations.enqueue(message, thread_id)
