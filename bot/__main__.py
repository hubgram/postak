import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from dotenv import load_dotenv
from openai import AsyncOpenAI

from bot.config import Settings
from bot.conversation import Conversations
from bot.handlers import discussion, new, new_from_group
from bot.store import SqliteDialogStore, create_store


async def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)

    store = create_store(settings.database_url, window=settings.history_window)
    if isinstance(store, SqliteDialogStore):
        await store.connect()

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )
    # The channel's linked discussion group; admins may run /new there too.
    discussion_group_id = (await bot.get_chat(settings.target_channel_id)).linked_chat_id
    conversations = Conversations(bot, client, settings.model, store, settings.target_channel_id)

    dp = Dispatcher()
    dp["store"] = store
    dp["target_channel_id"] = settings.target_channel_id
    dp["conversations"] = conversations
    dp.channel_post.register(new, Command("new"), F.chat.id == settings.target_channel_id)
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
