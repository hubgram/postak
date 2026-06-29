import os
from dataclasses import dataclass

SYSTEM_PROMPT = "You are a helpful assistant replying to comments on a Telegram post."
NEW_MESSAGE = "**💬 New Conversation**\n\n_Reply to this message to chat with AI_\\."
FIRST_PROMPT = (
    "This is the first message of a new conversation. On the first line, write a "
    "short title (3-6 words) for it. From the next line onward, write your answer."
)
TITLE_MAX = 100


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from the environment."""

    bot_token: str
    target_channel_id: int
    model: str
    llm_base_url: str | None
    llm_api_key: str
    database_url: str
    history_window: int

    @classmethod
    def from_env(cls) -> "Settings":
        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"{name} is not set. Add it to your .env file.")
            return value

        return cls(
            bot_token=required("BOT_TOKEN"),
            target_channel_id=int(required("TARGET_CHANNEL_ID")),
            model=required("LLM_MODEL"),
            llm_base_url=os.getenv("LLM_ENDPOINT") or None,
            llm_api_key=os.getenv("LLM_API_KEY") or "not-needed",
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///posttalk.db"),
            history_window=int(os.getenv("HISTORY_WINDOW", "20")),
        )
