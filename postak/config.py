import os
from dataclasses import dataclass

SYSTEM_PROMPT = (
    "You are a helpful assistant replying to comments on a Telegram post. "
    "Each user message carries its author as a name of the form Full_Name-123456, "
    "where underscores stand for spaces and the trailing number is the author's "
    "Telegram user id. Use it to tell commenters apart and address them by name. "
    "To tag a user, write [Full Name](tg://user?id=123456). A name without a "
    "numeric suffix is an anonymous sender and cannot be tagged."
)
NEW_MESSAGE = "**💬 New Conversation**\n\n_Reply to this message to start a conversation_\\."
FIRST_PROMPT = (
    "This is the first message of a new conversation. On the first line, write a "
    "short title (3-6 words) for it. From the next line onward, write your answer."
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
