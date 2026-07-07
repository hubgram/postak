"""Conversation-flow handlers: opening threads and answering comments."""

import contextlib

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, MessageOriginChannel, ReactionTypeEmoji

from postak.access import AccessPolicy
from postak.channels import register_channel
from postak.config import NEW_MESSAGE, SYSTEM_PROMPT
from postak.conversation import Conversations
from postak.registry import ChannelRegistry
from postak.store import DialogStore, Store


async def start_conversation(bot: Bot, channel_id: int, store: DialogStore) -> None:
    """Post the new-conversation message to the channel; its auto-forward opens a thread."""
    sent = await bot.send_message(channel_id, NEW_MESSAGE)
    await store.mark_pending((sent.chat.id, sent.message_id))


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


async def new_from_unlinked_group(
    message: Message,
    bot: Bot,
    store: Store,
    channel_registry: ChannelRegistry,
    access_policy: AccessPolicy,
) -> None:
    # /new in a group Postak doesn't yet know: a Postak admin can link its channel
    # and start the conversation in one step, instead of it doing nothing silently.
    if not await access_policy.can_manage(message):
        await message.reply("You are not a Postak admin.", parse_mode=None)
        return

    channel_id = await register_channel(message, message.chat.id, store, channel_registry)
    if channel_id is not None:
        await start_conversation(bot, channel_id, store)


def forwarded_channel_post(message: Message) -> tuple[int, int] | None:
    """(channel chat id, channel post id) behind an automatic forward, or None."""
    if isinstance(origin := message.forward_origin, MessageOriginChannel):
        return origin.chat.id, origin.message_id
    chat, post_id = message.forward_from_chat, message.forward_from_message_id
    if chat is not None and post_id is not None:
        return chat.id, post_id
    return None


async def open_discussion(message: Message, store: DialogStore) -> None:
    # A channel post is auto-forwarded into the discussion group as the root of
    # its comment thread. If it came from a /new post, open a dialog for it.
    origin = forwarded_channel_post(message)
    if origin is not None and await store.take_pending(origin):
        await store.start((message.chat.id, message.message_id), origin, system=SYSTEM_PROMPT)


async def answer_discussion(
    message: Message, conversations: Conversations, thread_id: int
) -> None:
    # Acknowledge receipt, then hand the comment to the per-thread batching worker.
    with contextlib.suppress(Exception):
        await message.react([ReactionTypeEmoji(emoji="👀")])
    conversations.enqueue(message, thread_id)
