from collections.abc import AsyncIterator
from typing import Protocol

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandObject
from aiogram.types import Message, MessageOriginChannel

from postak.access import AccessPolicy, AccessScope
from postak.config import NEW_MESSAGE, SYSTEM_PROMPT
from postak.conversation import Conversations, set_channel_title
from postak.generation import collect_tokens
from postak.llm import TitleSplitter, build_title_messages
from postak.store import DialogStore
from postak.store import Message as StoreMessage

POSTAK_USAGE = (
    "Usage: /postak admin|access|model|digest|compress|title|settitle|delete|regenerate ..."
)


class ModelController(Protocol):
    @property
    def generator(self) -> "CommandGenerator":
        """The generator used for operational command responses."""
        ...

    @property
    def model(self) -> str:
        """The model currently used for generations."""
        ...

    def set_model(self, model: str) -> object:
        """Change the model used for future generations."""
        ...


class CommandGenerator(Protocol):
    def tokens(self, messages: list[StoreMessage]) -> AsyncIterator[str]:
        """Stream an operational command response."""
        ...


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
    # Hand the comment to the per-thread batching worker.
    conversations.enqueue(message, thread_id)


async def postak_admin(
    message: Message,
    command: CommandObject,
    access_policy: AccessPolicy,
    pt: ModelController,
    store: DialogStore,
) -> None:
    """Manage Postak admins and access rules via /postak subcommands."""
    if not await access_policy.can_manage(message):
        await _reply(message, "You are not a Postak admin.")
        return

    args = (command.args or "").split()
    if not args:
        await _reply(message, POSTAK_USAGE)
        return

    try:
        match args:
            case ["admin", "add", user_id]:
                await access_policy.add_admin(_parse_user_id(user_id))
                await _reply(message, f"Added admin {user_id}.")
            case ["admin", "remove", user_id]:
                await access_policy.remove_admin(_parse_user_id(user_id))
                await _reply(message, f"Removed admin {user_id}.")
            case ["access", "allow", user_id, scope_name]:
                scope = _message_scope(message, scope_name)
                await access_policy.allow_user(_parse_user_id(user_id), scope)
                await _reply(message, f"Allowed user {user_id} for {scope.kind}.")
            case ["access", "revoke", user_id, scope_name]:
                scope = _message_scope(message, scope_name)
                await access_policy.revoke_user(_parse_user_id(user_id), scope)
                await _reply(message, f"Revoked user {user_id} for {scope.kind}.")
            case ["access", "public", "on", scope_name]:
                scope = _message_scope(message, scope_name)
                await access_policy.allow_everyone(scope)
                await _reply(message, f"Public access is on for {scope.kind}.")
            case ["access", "public", "off", scope_name]:
                scope = _message_scope(message, scope_name)
                await access_policy.restrict_everyone(scope)
                await _reply(message, f"Public access is off for {scope.kind}.")
            case ["model", "get"]:
                await _reply(message, f"Current model: {pt.model}.")
            case ["model", "set", model]:
                pt.set_model(model)
                await _reply(message, f"Model changed to {model}.")
            case ["digest"]:
                await _digest_thread(message, store, pt.generator)
            case ["compress"]:
                await _compress_thread(message, store, pt.generator)
            case ["title"]:
                await _regenerate_thread_title(message, store, pt.generator)
            case ["settitle", *title_parts]:
                await _set_thread_title(message, store, " ".join(title_parts).strip())
            case ["delete"]:
                await _delete_replied_message(message)
            case ["regenerate"]:
                await _regenerate_answer(message, store, pt.generator)
            case _:
                await _reply(message, "Unknown /postak command.")
    except (TypeError, ValueError) as exc:
        await _reply(message, str(exc))


def _parse_user_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid user id: {value!r}") from exc


def _message_scope(message: Message, scope: str) -> AccessScope:
    if scope == "global":
        return AccessScope.global_()
    if scope == "group":
        return AccessScope.group(message.chat.id)
    if scope == "thread":
        thread_id = discussion_thread_id(message)
        if thread_id is None:
            raise ValueError("Thread scope is only available inside a discussion thread")
        return AccessScope.thread(message.chat.id, thread_id)
    raise ValueError(f"Unknown access scope: {scope!r}")


async def _reply(message: Message, text: str) -> None:
    await message.reply(text, parse_mode=None)


async def _digest_thread(
    message: Message, store: DialogStore, generator: CommandGenerator
) -> None:
    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak digest inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    history = await store.history(key)
    digest_messages: list[StoreMessage] = [
        {
            "role": "system",
            "content": (
                "Summarize this Telegram discussion thread as a concise digest. "
                "Include the main points, decisions, and any open questions."
            ),
        },
        *[msg for msg in history if msg["role"] != "system"],
    ]
    digest = await collect_tokens(generator.tokens(digest_messages))
    await _reply(message, digest or "No digest generated.")


async def _compress_thread(
    message: Message, store: DialogStore, generator: CommandGenerator
) -> None:
    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak compress inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    history = await store.history(key)
    summary = await collect_tokens(generator.tokens(_compression_messages(history)))
    if not summary:
        await _reply(message, "No summary generated.")
        return

    compacted = _compacted_history(history, summary)
    await store.replace_history(key, compacted)
    await _reply(message, "Compressed thread history.")


async def _set_thread_title(message: Message, store: DialogStore, title: str) -> None:
    if not title:
        await _reply(message, "Usage: /postak settitle <text>")
        return

    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak settitle inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    await set_channel_title(message.bot, await store.channel_message(key), title)
    await _reply(message, f"Title changed to {title}.")


async def _regenerate_thread_title(
    message: Message, store: DialogStore, generator: CommandGenerator
) -> None:
    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak title inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    splitter = TitleSplitter(generator.tokens(build_title_messages(await store.history(key))))
    await collect_tokens(splitter.stream())
    if not splitter.title:
        await _reply(message, "No title generated.")
        return

    await set_channel_title(message.bot, await store.channel_message(key), splitter.title)
    await _reply(message, f"Title changed to {splitter.title}.")


async def _delete_replied_message(message: Message) -> None:
    target = message.reply_to_message
    if target is None:
        await _reply(message, "Reply to a message with /postak delete.")
        return
    if message.bot is None:
        await _reply(message, "Bot is not available for this message.")
        return

    await message.bot.delete_message(message.chat.id, target.message_id)
    await _reply(message, "Deleted message.")


async def _regenerate_answer(
    message: Message, store: DialogStore, generator: CommandGenerator
) -> None:
    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak regenerate inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    history = _history_through_latest_user(await store.history(key))
    if history is None:
        await _reply(message, "No user message to regenerate from.")
        return

    answer = await collect_tokens(generator.tokens(history))
    if not answer:
        await _reply(message, "No answer generated.")
        return

    await store.add(key, "assistant", answer)
    await _reply(message, answer)


def _history_through_latest_user(history: list[StoreMessage]) -> list[StoreMessage] | None:
    for index in range(len(history) - 1, -1, -1):
        if history[index]["role"] == "user":
            return history[: index + 1]
    return None


def _compression_messages(history: list[StoreMessage]) -> list[StoreMessage]:
    return [
        {
            "role": "system",
            "content": (
                "Compress this Telegram discussion into a durable memory summary for "
                "future assistant replies. Keep facts, decisions, preferences, open "
                "questions, and important context. Be concise."
            ),
        },
        *[message for message in history if message["role"] != "system"],
    ]


def _compacted_history(history: list[StoreMessage], summary: str) -> list[StoreMessage]:
    compacted = [
        {
            "role": "assistant",
            "content": f"Conversation summary so far:\n{summary}",
        }
    ]
    if history and history[0]["role"] == "system":
        return [history[0], *compacted]
    return compacted


def discussion_thread_id(message: Message) -> int | None:
    if message.message_thread_id is not None:
        return message.message_thread_id
    reply_to = message.reply_to_message
    return reply_to.message_id if reply_to is not None else None
