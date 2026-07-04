"""The model layer: a Generator streams an assistant reply for a dialog.

`Generator` is a Protocol so any LLM provider can be plugged in; `OpenAIGenerator`
is the default, backed by any OpenAI-compatible endpoint. Titling, history windowing
and delivery to Telegram stay in Postak, so a provider only yields text deltas.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol, cast, runtime_checkable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from postak.store import Message


class Generator(Protocol):
    def tokens(self, messages: list[Message]) -> AsyncIterator[str]:
        """Stream the assistant reply token-by-token for this dialog."""
        ...


@runtime_checkable
class ModelConfigurable(Protocol):
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
        **params: Any,
    ) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
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
            messages=cast(list[ChatCompletionMessageParam], messages),
            stream=True,
            **self._params,
        )
        async for chunk in stream:
            if delta := chunk.choices[0].delta.content:
                yield delta
