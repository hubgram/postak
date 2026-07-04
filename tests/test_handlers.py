import unittest
from collections.abc import AsyncIterator
from types import SimpleNamespace

from postak.handlers import postak_admin
from postak.store import InMemoryDialogStore


class FakeAccessPolicy:
    async def can_manage(self, message) -> bool:
        return True


class FakePostak:
    def __init__(self) -> None:
        self.model = "old"
        self.generator = FakeGenerator("digest text")

    def set_model(self, model: str) -> "FakePostak":
        self.model = model
        return self


class FakeGenerator:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages = []

    async def tokens(self, messages) -> AsyncIterator[str]:
        self.messages = messages
        yield self.response


class FakeMessage:
    def __init__(self, *, chat_id: int = 10, thread_id: int | None = 20) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.message_thread_id = thread_id
        self.replies: list[str] = []

    async def answer(self, text: str, parse_mode=None) -> None:
        self.replies.append(text)


class PostakAdminHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_set_command_changes_runtime_model(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="model set next")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(pt.model, "next")
        self.assertEqual(message.replies, ["Model changed to next."])

    async def test_digest_command_replies_with_thread_digest(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        command = SimpleNamespace(args="digest")
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "first")
        await store.add((10, 20), "assistant", "second")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.replies, ["digest text"])
        self.assertEqual(pt.generator.messages[-2:], [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ])

    async def test_digest_command_requires_postak_thread(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="digest")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["This thread is not a Postak conversation."])


if __name__ == "__main__":
    unittest.main()
