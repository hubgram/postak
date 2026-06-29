from collections.abc import AsyncIterator
from typing import cast

from aiogram.types import Message
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from bot.config import FIRST_PROMPT, SYSTEM_PROMPT, TITLE_MAX
from bot.rendering import stream_tokens


async def completion_tokens(
    client: AsyncOpenAI, model: str, messages: list[dict[str, str]]
) -> AsyncIterator[str]:
    completion = await client.chat.completions.create(
        model=model,
        messages=cast(list[ChatCompletionMessageParam], messages),
        stream=True,
    )
    async for chunk in completion:
        if delta := chunk.choices[0].delta.content:
            yield delta


async def stream_answer(
    message: Message, client: AsyncOpenAI, model: str, messages: list[dict[str, str]]
) -> str:
    return await stream_tokens(message, completion_tokens(client, model, messages))


class TitleSplitter:
    """Wraps a token stream: captures the first line as the title, streams the rest."""

    def __init__(self, tokens: AsyncIterator[str]) -> None:
        self._tokens = tokens
        self.title = ""

    async def stream(self) -> AsyncIterator[str]:
        buffer = ""
        streaming = False
        async for token in self._tokens:
            if streaming:
                yield token
                continue
            buffer += token
            if "\n" in buffer:
                title, _, rest = buffer.partition("\n")
                self.title = title.strip()[:TITLE_MAX]
                streaming = True
                if rest:
                    yield rest
        if not streaming:  # no newline arrived: the whole reply was the title line
            self.title = buffer.strip()[:TITLE_MAX]


def is_first_message(history: list[dict[str, str]]) -> bool:
    """True when the assistant has not yet replied in this thread."""
    return not any(m["role"] == "assistant" for m in history)


def build_title_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """History with the title-generation instruction folded into the system prompt."""
    system = f"{SYSTEM_PROMPT}\n\n{FIRST_PROMPT}"
    return [{"role": "system", "content": system}, *(m for m in history if m["role"] != "system")]
