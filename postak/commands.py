"""The /postak admin command suite."""

from collections.abc import AsyncIterator
from typing import Protocol

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.types import Message

from postak.access import AccessPolicy, AccessScope
from postak.channels import register_channel
from postak.config import FIRST_PROMPT
from postak.conversation import set_channel_title
from postak.generation import collect_tokens
from postak.registry import ChannelRegistry
from postak.rendering import stream_tokens
from postak.store import GLOBAL_PROMPT, AccessKey, DialogStore, Store
from postak.store import Message as StoreMessage
from postak.titling import TitleSplitter, build_title_messages

POSTAK_USAGE = (
    "Usage: /postak admin|access|add|remove|model|sysprompt|digest|compress|title|settitle|"
    "delete|regenerate ...\n"
    "See /postak help for details."
)
POSTAK_HELP = """/postak commands:

Access
- admin list
- admin add|remove <user_id>
- access list
- access allow|revoke <user_id> global|group|thread
- access public on|off global|group|thread

Channels
- add [chat_id] - serve the current chat's channel (or a given channel/group id)
- remove [chat_id] - stop serving it and drop its access rules

In a thread
- digest - summarize the conversation
- compress - replace history with a compact summary
- title - regenerate the post title
- settitle <text> - set the title yourself
- regenerate - redo the last answer
- delete - delete the replied-to message

Model
- model get
- model set <model>

Prompt
- sysprompt - show the global system prompt
- sysprompt <text> - set it (used by new conversations)
- sysprompt delete - reset to the built-in default"""

TELEGRAM_TEXT_LIMIT = 4096


class PostakController(Protocol):
    @property
    def generator(self) -> "CommandGenerator":
        """The generator used for operational command responses."""
        ...

    @property
    def model(self) -> str:
        """The model currently used for generations."""
        ...

    @property
    def title_prompt(self) -> str:
        """Instruction used when generating the first reply and post title."""
        ...

    @property
    def system_prompt(self) -> str:
        """The built-in default system prompt for new conversations."""
        ...

    @property
    def channel_registry(self) -> ChannelRegistry:
        """The live registry of served channels and their discussion groups."""
        ...

    def set_model(self, model: str) -> object:
        """Change the model used for future generations."""
        ...


class CommandGenerator(Protocol):
    def tokens(self, messages: list[StoreMessage]) -> AsyncIterator[str]:
        """Stream an operational command response."""
        ...


async def postak_admin(
    message: Message,
    command: CommandObject,
    access_policy: AccessPolicy,
    pt: PostakController,
    store: Store,
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
            case ["help"]:
                await _reply(message, POSTAK_HELP)
            case ["admin", "list"]:
                admins = await access_policy.admins()
                await _reply(
                    message, "Admins: " + ", ".join(map(str, admins)) if admins else "No admins."
                )
            case ["access", "list"]:
                await _reply(message, await _format_access_rules(access_policy))
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
            case ["add"]:
                await register_channel(message, message.chat.id, store, pt.channel_registry)
            case ["add", chat_id]:
                await register_channel(
                    message, _parse_chat_id(chat_id), store, pt.channel_registry
                )
            case ["remove"]:
                await _remove_channel(
                    message, message.chat.id, store, pt.channel_registry, access_policy
                )
            case ["remove", chat_id]:
                await _remove_channel(
                    message, _parse_chat_id(chat_id), store, pt.channel_registry, access_policy
                )
            case ["model", "get"]:
                await _reply(message, f"Current model: {pt.model}.")
            case ["model", "set", model]:
                pt.set_model(model)
                await _reply(message, f"Model changed to {model}.")
            case ["sysprompt"]:
                stored = await store.get_system_prompt(GLOBAL_PROMPT)
                await _reply(message, stored or f"Default system prompt:\n{pt.system_prompt}")
            case ["sysprompt", "delete"]:
                await store.delete_system_prompt(GLOBAL_PROMPT)
                await _reply(message, "System prompt reset to default.")
            case ["sysprompt", *_]:
                text = (command.args or "").strip().removeprefix("sysprompt").strip()
                await store.set_system_prompt(GLOBAL_PROMPT, text)
                await _reply(message, "System prompt updated. New conversations will use it.")
            case ["digest"]:
                await _digest_thread(message, store, pt.generator)
            case ["compress"]:
                await _compress_thread(message, store, pt.generator)
            case ["title"]:
                await _regenerate_thread_title(message, store, pt.generator, pt.title_prompt)
            case ["settitle", *title_parts]:
                await _set_thread_title(message, store, " ".join(title_parts).strip())
            case ["delete"]:
                await _delete_replied_message(message)
            case ["regenerate"]:
                await _regenerate_answer(message, store, pt.generator)
            case _:
                await _reply(message, "Unknown /postak command. Try /postak help.")
    except (TypeError, ValueError) as exc:
        await _reply(message, str(exc))
    except TelegramBadRequest as exc:
        await _reply(message, f"Telegram error: {exc.message}")


async def _format_access_rules(access_policy: AccessPolicy) -> str:
    lines = [
        f"public {_format_scope(scope)}: {'on' if public else 'off'}"
        for scope, public in await access_policy.public_scopes()
    ]
    lines += [
        f"user {user_id}: {_format_scope(scope)}"
        for user_id, scope in await access_policy.allowed_users()
    ]
    return "\n".join(lines) or "No access rules."


def _format_scope(key: AccessKey) -> str:
    kind, chat_id, thread_id = key
    return " ".join([kind, *(str(part) for part in (chat_id, thread_id) if part)])


def _parse_user_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid user id: {value!r}") from exc


def _parse_chat_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid chat id: {value!r}") from exc


async def _remove_channel(
    message: Message,
    chat_id: int,
    store: Store,
    channels: ChannelRegistry,
    access_policy: AccessPolicy,
) -> None:
    removed = await store.remove_channel(chat_id)
    if removed is None:
        await _reply(message, f"No channel registered for {chat_id}.")
        return

    channel_id, group_id = removed
    channels.remove(channel_id)
    await access_policy.clear_chat(channel_id)
    await access_policy.clear_chat(group_id)
    await _reply(message, f"Removed channel {channel_id} (group {group_id}).")


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
    for start in range(0, len(text), TELEGRAM_TEXT_LIMIT):
        await message.reply(text[start : start + TELEGRAM_TEXT_LIMIT], parse_mode=None)


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
    digest = await stream_tokens(message, generator.tokens(digest_messages))
    if not digest:
        await _reply(message, "No digest generated.")


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
    message: Message,
    store: DialogStore,
    generator: CommandGenerator,
    title_prompt: str = FIRST_PROMPT,
) -> None:
    thread_id = discussion_thread_id(message)
    if thread_id is None:
        await _reply(message, "Run /postak title inside a discussion thread.")
        return

    key = (message.chat.id, thread_id)
    if not await store.has(key):
        await _reply(message, "This thread is not a Postak conversation.")
        return

    splitter = TitleSplitter(
        generator.tokens(build_title_messages(await store.history(key), title_prompt))
    )
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

    answer = await stream_tokens(message, generator.tokens(history))
    if not answer:
        await _reply(message, "No answer generated.")
        return

    await store.add(key, "assistant", answer)


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
    compacted: list[StoreMessage] = [
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
