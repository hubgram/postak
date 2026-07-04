import unittest
from types import SimpleNamespace

from postak.handlers import postak_admin


class FakeAccessPolicy:
    async def can_manage(self, message) -> bool:
        return True


class FakePostak:
    def __init__(self) -> None:
        self.model = "old"

    def set_model(self, model: str) -> "FakePostak":
        self.model = model
        return self


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def answer(self, text: str, parse_mode=None) -> None:
        self.replies.append(text)


class PostakAdminHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_set_command_changes_runtime_model(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="model set next")

        await postak_admin(message, command, FakeAccessPolicy(), pt)

        self.assertEqual(pt.model, "next")
        self.assertEqual(message.replies, ["Model changed to next."])


if __name__ == "__main__":
    unittest.main()
