"""Resolving and registering channel <-> discussion group links."""

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import Message

from postak.registry import ChannelRegistry
from postak.store import Store

NOT_LINKED = (
    "This chat isn't linked to a channel/discussion group on Telegram yet. "
    "Link them in the channel's Discussion settings first."
)


async def resolve_channel_link(bot: Bot, chat_id: int) -> tuple[int, int] | None:
    """Resolve (channel_id, discussion_group_id) from either side of the pair."""
    chat = await bot.get_chat(chat_id)
    if chat.linked_chat_id is None:
        return None
    if chat.type == ChatType.CHANNEL:
        return chat.id, chat.linked_chat_id
    return chat.linked_chat_id, chat.id


async def bot_can_post(bot: Bot, channel_id: int) -> bool:
    member = await bot.get_chat_member(channel_id, bot.id)
    if member.status == ChatMemberStatus.CREATOR:
        return True
    if member.status == ChatMemberStatus.ADMINISTRATOR:
        return bool(getattr(member, "can_post_messages", False))
    return False


async def register_channel(
    message: Message, chat_id: int, store: Store, channels: ChannelRegistry
) -> int | None:
    """Resolve, validate and persist a channel link; reply with the outcome.

    Returns the channel id on success, None if it couldn't be registered.
    """
    if message.bot is None:
        await message.reply("Bot is not available for this message.", parse_mode=None)
        return None

    link = await resolve_channel_link(message.bot, chat_id)
    if link is None:
        await message.reply(NOT_LINKED, parse_mode=None)
        return None

    channel_id, group_id = link
    if not await bot_can_post(message.bot, channel_id):
        await message.reply(
            f"Postak isn't an admin with 'Post Messages' rights in channel {channel_id}. "
            "Grant it that permission, then try again.",
            parse_mode=None,
        )
        return None

    await store.add_channel(channel_id, group_id)
    channels.add(channel_id)
    channels.link_discussion(channel_id, group_id)
    await message.reply(
        f"Added channel {channel_id} linked to group {group_id}.", parse_mode=None
    )
    return channel_id
