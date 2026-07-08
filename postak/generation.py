"""The model layer: a Generator streams an assistant reply for a dialog.

`Generator` is a Protocol so any LLM provider can be plugged in; `OpenAIGenerator`
is the default, backed by any OpenAI-compatible endpoint. Titling, history windowing
and delivery to Telegram stay in Postak, so a provider only yields text deltas.
"""

import re
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast, runtime_checkable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from postak.store import Message

# The API rejects participant names containing whitespace or any of < | \ / >.
_NAME_FORBIDDEN = re.compile(r"[\s<|\\/>]+")
_NAME_MAX = 64


class Generator(Protocol):
    def tokens(self, messages: list[Message]) -> AsyncIterator[str]:
        """Stream the assistant reply token-by-token for this dialog."""
        ...


def _participant_name(name: str, user_id: int | None) -> str:
    sanitized = _NAME_FORBIDDEN.sub("_", name).strip("_") or "Unknown"
    if user_id is None:
        return sanitized[:_NAME_MAX]
    suffix = f"-{user_id}"
    return sanitized[: _NAME_MAX - len(suffix)] + suffix


def prepare_messages(messages: list[Message]) -> list[ChatCompletionMessageParam]:
    """Move stored author identity into the API's per-message name field."""
    prepared: list[ChatCompletionMessageParam] = []
    for message in messages:
        item: dict[str, str] = {"role": message["role"], "content": message["content"]}
        if (name := message.get("user_name")) is not None:
            item["name"] = _participant_name(name, message.get("user_id"))
        prepared.append(cast(ChatCompletionMessageParam, item))
    return prepared


async def collect_tokens(tokens: AsyncIterator[str]) -> str:
    """Collect a token stream into a single string."""
    chunks: list[str] = []
    async for token in tokens:
        chunks.append(token)
    return "".join(chunks)


@runtime_checkable
class ModelConfigurable(Protocol):
    @property
    def model(self) -> str:
        """The model used for generations."""
        ...

    def set_model(self, model: str) -> None:
        """Change the model used for future generations."""
        ...


class OpenAIGenerator:
    """Default Generator backed by any OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str = "not-needed",
        timeout: float = 120.0,
        **params: Any,
    ) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model
        self._params = params  # extra sampling params: temperature, max_tokens, ...

    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        self._model = model

    async def tokens(self, messages: list[Message]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=prepare_messages(messages),
            stream=True,
            **self._params,
        )
        async for chunk in stream:
            if chunk.choices and (delta := chunk.choices[0].delta.content):
                yield delta
