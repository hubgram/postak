import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv


async def new(message: Message) -> None:
    await message.answer("New!")


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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
