import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from postak.app import Postak
from postak.config import Settings
from postak.generation import OpenAIGenerator
from postak.store import create_store


async def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    generator = OpenAIGenerator(
        model=settings.model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    postak = Postak(
        generator=generator,
        store=create_store(settings.database_url, window=settings.history_window),
        channels=[settings.target_channel_id],
        admins=settings.admins,
    )

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )
    dp = Dispatcher()
    postak.attach(dp)
    await dp.start_polling(bot)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
