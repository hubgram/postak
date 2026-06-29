import asyncio
import os
from typing import cast

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import InputRichMessage, Message, MessageOriginChannel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from telegramify_markdown.stream import EditStream

from bot.store import DialogStore, InMemoryDialogStore, SqliteDialogStore

SYSTEM_PROMPT = "You are a helpful assistant replying to comments on a Telegram post."
NEW_MESSAGE = "**💬 New Conversation**\n\n_Reply to this message to chat with AI_\\."


async def new(message: Message, store: DialogStore) -> None:
    sent = await message.answer(NEW_MESSAGE)
    await store.mark_pending(sent.message_id)


def forwarded_channel_post_id(message: Message) -> int | None:
    """Original channel post id behind an automatic forward, or None."""
    if isinstance(origin := message.forward_origin, MessageOriginChannel):
        return origin.message_id
    return message.forward_from_message_id


async def stream_answer(
    message: Message, client: AsyncOpenAI, model: str, messages: list[dict[str, str]]
) -> str:
    """Stream the LLM answer into one reply, editing it live. Returns the full text."""
    completion = await client.chat.completions.create(
        model=model,
        messages=cast(list[ChatCompletionMessageParam], messages),
        stream=True,
    )

    bot = message.bot
    assert bot is not None  # always set on messages received in a handler

    async def tokens():
        async for chunk in completion:
            if delta := chunk.choices[0].delta.content:
                yield delta

    async def send_message(payload) -> int:
        sent = await message.reply_rich(InputRichMessage(**payload.rich_message.to_dict()))
        return sent.message_id

    async def edit_message(message_id: int, payload) -> None:
        await bot.edit_message_text(
            rich_message=InputRichMessage(**payload.rich_message.to_dict()),
            chat_id=message.chat.id,
            message_id=message_id,
        )

    # rich mode: send the first rich message, then stream edits via
    # editMessageText(rich_message=...). EditStream throttles to Telegram's 1s limit.
    async with EditStream(send_message, edit_message, mode="rich") as stream:
        await stream.consume(tokens())
    return stream.buffer


async def discussion(message: Message, client: AsyncOpenAI, model: str, store: DialogStore) -> None:
    # A channel post is auto-forwarded into the discussion group as the root of
    # its comment thread. If it came from a /new post, open a dialog for it.
    if message.is_automatic_forward:
        channel_post_id = forwarded_channel_post_id(message)
        if channel_post_id is not None and await store.take_pending(channel_post_id):
            await store.start(message.message_id, system=SYSTEM_PROMPT)
        return

    # Stream the answer into a single reply, using the windowed dialog as context.
    thread_id = message.message_thread_id
    if thread_id is not None and message.text and await store.has(thread_id):
        await store.add(thread_id, "user", message.text)
        history = await store.history(thread_id)
        reply = await stream_answer(message, client, model, history)
        await store.add(thread_id, "assistant", reply)


def build_store() -> DialogStore:
    window = int(os.getenv("HISTORY_WINDOW", "20"))
    if os.getenv("STORE", "sqlite") == "memory":
        return InMemoryDialogStore(window=window)
    return SqliteDialogStore(os.getenv("SQLITE_PATH", "posttalk.db"), window=window)


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

    dp = Dispatcher()
    dp["client"] = client
    dp["model"] = model
    dp["store"] = store
    dp.channel_post.register(new, Command("new"), F.chat.id == target_channel_id)
    dp.message.register(discussion, F.chat.type == "supergroup")

    try:
        await dp.start_polling(bot)
    finally:
        if isinstance(store, SqliteDialogStore):
            await store.close()


if __name__ == "__main__":
    asyncio.run(main())
