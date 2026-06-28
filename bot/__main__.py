import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, MessageOriginChannel
from dotenv import load_dotenv


# Channel post id -> discussion thread id.
threads: dict[int, int | None] = {}
# Thread id -> number of comments counted so far.
counts: dict[int, int] = {}


async def new(message: Message) -> None:
    sent = await message.answer("New!")
    threads[sent.message_id] = None


def forwarded_channel_post_id(message: Message) -> int | None:
    if isinstance(origin := message.forward_origin, MessageOriginChannel):
        return origin.message_id
    return message.forward_from_message_id


async def discussion(message: Message) -> None:
    if message.is_automatic_forward:
        if (channel_post_id := forwarded_channel_post_id(message)) in threads:
            thread_id = message.message_id
            threads[channel_post_id] = thread_id
            counts[thread_id] = 0
        return

    if (thread_id := message.message_thread_id) in counts:
        counts[thread_id] += 1
        await message.reply(str(counts[thread_id]))


async def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to your .env file.")

    raw_channel_id = os.getenv("TARGET_CHANNEL_ID")
    if not raw_channel_id:
        raise RuntimeError("TARGET_CHANNEL_ID is not set. Add it to your .env file.")
    target_channel_id = int(raw_channel_id)

    bot = Bot(token=token)

    dp = Dispatcher()
    dp.channel_post.register(new, Command("new"), F.chat.id == target_channel_id)
    dp.message.register(discussion, F.chat.type == "supergroup")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
