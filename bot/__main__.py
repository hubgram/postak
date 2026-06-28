import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import InputRichMessage, Message, MessageOriginChannel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegramify_markdown import telegramify_rich

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


async def answer(client: AsyncOpenAI, model: str, messages: list[dict[str, str]]) -> str:
    response = await client.chat.completions.create(model=model, messages=messages)
    return response.choices[0].message.content or ""


async def send_rich(message: Message, text: str) -> None:
    """Convert Markdown to Telegram Rich Messages and reply with each chunk."""
    for rich_text in telegramify_rich(text):
        await message.reply_rich(InputRichMessage(**rich_text.to_dict()))


async def discussion(message: Message, client: AsyncOpenAI, model: str) -> None:
    # A channel post is auto-forwarded into the discussion group as the root of
    # its comment thread. If it came from a /new post, map channel id -> thread id
    # and open a dialog for it.
    if message.is_automatic_forward:
        if (channel_post_id := forwarded_channel_post_id(message)) in threads:
            thread_id = message.message_id
            threads[channel_post_id] = thread_id
            store.start(thread_id, system=SYSTEM_PROMPT)
        return

    # Answer each comment in a tracked thread, keeping the dialog as context.
    thread_id = message.message_thread_id
    if store.has(thread_id) and message.text:
        store.add(thread_id, "user", message.text)
        reply = await answer(client, model, store.messages(thread_id))
        store.add(thread_id, "assistant", reply)
        # LLM output is standard Markdown; telegramify_rich() converts it to
        # Telegram Rich Messages (HTML) sent via the sendRichMessage API.
        await send_rich(message, reply)


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
