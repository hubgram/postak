from dotenv import load_dotenv

from postak.app import Postak
from postak.config import Settings
from postak.generation import OpenAIGenerator
from postak.store import create_store


def main() -> None:
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
        channels=settings.target_channel_ids,
        admins=settings.admins,
    )
    postak.run(settings.bot_token)


if __name__ == "__main__":
    main()
