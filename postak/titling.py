"""Titling a conversation from the first exchange."""

from collections.abc import AsyncIterator

from postak.config import FIRST_PROMPT, SYSTEM_PROMPT, TITLE_MAX
from postak.store import Message


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


def is_first_message(history: list[Message]) -> bool:
    """True when the assistant has not yet replied in this thread."""
    return not any(m["role"] == "assistant" for m in history)


def build_title_messages(history: list[Message]) -> list[Message]:
    """History with the title-generation instruction folded into the system prompt."""
    system = f"{SYSTEM_PROMPT}\n\n{FIRST_PROMPT}"
    return [{"role": "system", "content": system}, *(m for m in history if m["role"] != "system")]
