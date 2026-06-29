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

from bot.store import DialogStore

SYSTEM_PROMPT = "You are a helpful assistant replying to comments on a Telegram post."
NEW_MESSAGE = "**💬 New Conversation**\n\n_Reply to this message to chat with AI_\\."

# Channel post id -> discussion thread id.
threads: dict[int, int | None] = {}
# Per-thread chat dialogs.
store = DialogStore()


async def new(message: Message) -> None:
    sent = await message.answer(NEW_MESSAGE)
    threads[sent.message_id] = None


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


async def discussion(message: Message, client: AsyncOpenAI, model: str) -> None:
    # A channel post is auto-forwarded into the discussion group as the root of
    # its comment thread. If it came from a /new post, map channel id -> thread id
    # and open a dialog for it.
    if message.is_automatic_forward:
        if (channel_post_id := forwarded_channel_post_id(message)) in threads:
            new_thread_id = message.message_id
            threads[channel_post_id] = new_thread_id
            store.start(new_thread_id, system=SYSTEM_PROMPT)
        return

    # Stream the answer into a single reply that is edited as tokens arrive.
    thread_id = message.message_thread_id
    if thread_id is not None and store.has(thread_id) and message.text:
        store.add(thread_id, "user", message.text)
        reply = await stream_answer(message, client, model, store.messages(thread_id))
        store.add(thread_id, "assistant", reply)


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

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )

    dp = Dispatcher()
    dp["client"] = client
    dp["model"] = model
    dp.channel_post.register(new, Command("new"), F.chat.id == target_channel_id)
    dp.message.register(discussion, F.chat.type == "supergroup")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
