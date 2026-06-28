import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv


async def start(message: Message) -> None:
    await message.answer("Hi!")


async def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to your .env file.")

    bot = Bot(token=token)

    dp = Dispatcher()
    dp.message.register(start, CommandStart())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
