import os
from dataclasses import dataclass

SYSTEM_PROMPT = (
    "You reply in a Telegram discussion thread. User messages may have a `name` "
    "like `Full_Name-123456`: underscores mean spaces and the number is the user ID. "
    "Use it only to distinguish people. To mention a user with an ID, write "
    "`[Full Name](tg://user?id=123456)`; otherwise, do not tag them."
)
NEW_MESSAGE = "**💬 New Conversation**"
NEW_CONVERSATION_GREETINGS = (
    "What would you like to talk about?",
    "What's on your mind?",
    "Where would you like to begin?",
    "How can I help today?",
    "What are we exploring today?",
    "What can I help you work through?",
    "What would you like to figure out?",
    "What are you thinking about?",
    "Where should we start?",
    "What can I help with?",
)
NEW_CONVERSATION_CREATOR_TEMPLATE = "Hi {user}, {greeting}"
FIRST_PROMPT = (
    "First reply only: write a 3-6 word title on the first line, then the reply. "
    "Do not label either."
)
TITLE_MAX = 100


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from the environment."""

    bot_token: str
    target_channel_ids: list[int]
    model: str
    llm_base_url: str | None
    llm_api_key: str
    database_url: str
    history_window: int
    admins: list[int]

    @classmethod
    def from_env(cls) -> "Settings":
        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"{name} is not set. Add it to your .env file.")
            return value

        return cls(
            bot_token=required("BOT_TOKEN"),
            target_channel_ids=_parse_int_list(os.getenv("TARGET_CHANNEL_ID", "")),
            model=required("LLM_MODEL"),
            llm_base_url=os.getenv("LLM_ENDPOINT") or None,
            llm_api_key=os.getenv("LLM_API_KEY") or "not-needed",
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///postak.db"),
            history_window=int(os.getenv("HISTORY_WINDOW", "20")),
            admins=_parse_int_list(os.getenv("POSTAK_ADMINS", "")),
        )


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]
