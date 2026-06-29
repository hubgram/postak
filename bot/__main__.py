import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import cast

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InputRichMessage, Message, MessageOriginChannel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from telegramify_markdown.stream import EditStream

from bot.store import DialogStore, SqliteDialogStore, create_store

SYSTEM_PROMPT = "You are a helpful assistant replying to comments on a Telegram post."
NEW_MESSAGE = "**💬 New Conversation**\n\n_Reply to this message to chat with AI_\\."
FIRST_PROMPT = (
    "This is the first message of a new conversation. On the first line, write a "
    "short title (3-6 words) for it. From the next line onward, write your answer."
)
TITLE_MAX = 100


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


async def completion_tokens(
    client: AsyncOpenAI, model: str, messages: list[dict[str, str]]
) -> AsyncIterator[str]:
    completion = await client.chat.completions.create(
        model=model,
        messages=cast(list[ChatCompletionMessageParam], messages),
        stream=True,
    )
    async for chunk in completion:
        if delta := chunk.choices[0].delta.content:
            yield delta


async def stream_tokens(message: Message, tokens: AsyncIterator[str]) -> str:
    """Stream an async token iterator into one reply via EditStream; return the text."""
    bot = message.bot
    assert bot is not None  # always set on messages received in a handler

    async def send_message(payload) -> int:
        sent = await message.reply_rich(InputRichMessage(**payload.rich_message.to_dict()))
        return sent.message_id

    async def edit_message(message_id: int, payload) -> None:
        await bot.edit_message_text(
            rich_message=InputRichMessage(**payload.rich_message.to_dict()),
            chat_id=message.chat.id,
            message_id=message_id,
        )

    async with EditStream(send_message, edit_message, mode="rich") as stream:
        await stream.consume(tokens)
    return stream.buffer


async def stream_answer(
    message: Message, client: AsyncOpenAI, model: str, messages: list[dict[str, str]]
) -> str:
    return await stream_tokens(message, completion_tokens(client, model, messages))


class TitleSplitter:
    """Wraps a token stream: captures the first line as the title, streams the rest."""

    def __init__(self, tokens: AsyncIterator[str]) -> None:
        self._tokens = tokens
        self.title = ""

    async def stream(self) -> AsyncIterator[str]:
        buffer = ""
        streaming = False
        async for token in self._tokens:
            if streaming:
                yield token
                continue
            buffer += token
            if "\n" in buffer:
                title, _, rest = buffer.partition("\n")
                self.title = title.strip()[:TITLE_MAX]
                streaming = True
                if rest:
                    yield rest
        if not streaming:  # no newline arrived: the whole reply was the title line
            self.title = buffer.strip()[:TITLE_MAX]


def is_first_message(history: list[dict[str, str]]) -> bool:
    """True when the assistant has not yet replied in this thread."""
    return not any(m["role"] == "assistant" for m in history)


def build_title_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """History with the title-generation instruction folded into the system prompt."""
    system = f"{SYSTEM_PROMPT}\n\n{FIRST_PROMPT}"
    return [{"role": "system", "content": system}, *(m for m in history if m["role"] != "system")]


async def set_channel_title(bot: Bot, channel_id: int, message_id: int | None, title: str) -> None:
    if message_id is None or not title:
        return
    # Ignore edit failures: channel post deleted, not editable, or unchanged.
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_text(
            title, chat_id=channel_id, message_id=message_id, parse_mode=None
        )


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
    if thread_id is not None and message.text and await store.has(thread_id):
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


def build_store() -> DialogStore:
    return create_store(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///posttalk.db"),
        window=int(os.getenv("HISTORY_WINDOW", "20")),
    )


async def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to your .env file.")

    raw_channel_id = os.getenv("TARGET_CHANNEL_ID")
    if not raw_channel_id:
        raise RuntimeError("TARGET_CHANNEL_ID is not set. Add it to your .env file.")
    target_channel_id = int(raw_channel_id)

    model = os.getenv("LLM_MODEL")
    if not model:
        raise RuntimeError("LLM_MODEL is not set. Add it to your .env file.")
    client = AsyncOpenAI(
        base_url=os.getenv("LLM_ENDPOINT") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed",
    )

    store = build_store()
    if isinstance(store, SqliteDialogStore):
        await store.connect()

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )

    discussion_group_id = (await bot.get_chat(target_channel_id)).linked_chat_id

    dp = Dispatcher()
    dp["client"] = client
    dp["model"] = model
    dp["store"] = store
    dp["target_channel_id"] = target_channel_id
    dp.channel_post.register(new, Command("new"), F.chat.id == target_channel_id)
    if discussion_group_id is not None:
        dp.message.register(new_from_group, Command("new"), F.chat.id == discussion_group_id)
    dp.message.register(discussion, F.chat.type == "supergroup")

    try:
        await dp.start_polling(bot)
    finally:
        if isinstance(store, SqliteDialogStore):
            await store.close()


if __name__ == "__main__":
    asyncio.run(main())
