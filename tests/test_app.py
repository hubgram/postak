import unittest
from collections.abc import AsyncIterator

from aiogram import Dispatcher

from postak.app import Postak
from postak.store import InMemoryDialogStore


class StubGenerator:
    def __init__(self) -> None:
        self.model = "stub-model"

    def set_model(self, model: str) -> None:
        self.model = model

    async def _tokens(self) -> AsyncIterator[str]:
        yield "ok"

    def tokens(self, messages) -> AsyncIterator[str]:
        return self._tokens()


class RecordingPostak(Postak):
    def __init__(self) -> None:
        super().__init__(generator=StubGenerator(), store=InMemoryDialogStore())
        self.started = False
        self.stopped = False

    async def on_startup(self, bot) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.stopped = True


class PostakAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_add_channel_updates_channel_registry(self) -> None:
        postak = RecordingPostak()

        postak.add_channel(10).add_channel(10).add_channel(20)

        self.assertEqual(postak.channels, [10, 20])

    async def test_admin_methods_update_admin_registry(self) -> None:
        postak = RecordingPostak()

        postak.add_admin(1).remove_admin(1).add_admin(2)

        self.assertEqual(postak.admin_registry.admins, {2})
        self.assertEqual(postak.admin_registry.removals, {1})

    async def test_set_model_updates_configurable_generator(self) -> None:
        postak = RecordingPostak()

        postak.set_model("next-model")

        self.assertEqual(postak.generator.model, "next-model")

    async def test_attach_registers_dispatcher_lifecycle_hooks(self) -> None:
        postak = RecordingPostak()
        dp = Dispatcher()

        postak.attach(dp)

        startup_callbacks = [handler.callback for handler in dp.startup.handlers]
        shutdown_callbacks = [handler.callback for handler in dp.shutdown.handlers]

        self.assertIn(postak._startup, startup_callbacks)
        self.assertIn(postak._shutdown, shutdown_callbacks)

    async def test_dispatcher_lifecycle_hooks_call_explicit_methods(self) -> None:
        postak = RecordingPostak()

        await postak._startup(bot=object())
        await postak._shutdown()

        self.assertTrue(postak.started)
        self.assertTrue(postak.stopped)


if __name__ == "__main__":
    unittest.main()
