import unittest
from collections.abc import AsyncIterator
from types import SimpleNamespace

from aiogram import Dispatcher

from postak.app import InServedChannel, Postak
from postak.registry import ChannelRegistry
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


class FakeBot:
    def __init__(self, linked_chat_id: int | None = None) -> None:
        self._linked_chat_id = linked_chat_id
        self.commands = None

    async def get_chat(self, chat_id: int) -> SimpleNamespace:
        return SimpleNamespace(linked_chat_id=self._linked_chat_id)

    async def set_my_commands(self, commands) -> None:
        self.commands = commands


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

    async def test_on_startup_persists_env_configured_channel_link(self) -> None:
        store = InMemoryDialogStore()
        postak = Postak(generator=StubGenerator(), store=store, channels=[10])

        await postak.on_startup(FakeBot(linked_chat_id=20))

        self.assertEqual(postak.channel_registry.channel_for_discussion(20), 10)
        self.assertEqual(await store.channel_links(), [(10, 20)])

    async def test_on_startup_loads_channel_links_persisted_earlier(self) -> None:
        store = InMemoryDialogStore()
        await store.add_channel(30, 40)
        postak = Postak(generator=StubGenerator(), store=store)

        await postak.on_startup(FakeBot())

        self.assertEqual(postak.channels, [30])
        self.assertEqual(postak.channel_registry.channel_for_discussion(40), 30)


class InServedChannelTest(unittest.IsolatedAsyncioTestCase):
    async def test_reflects_channels_added_after_construction(self) -> None:
        registry = ChannelRegistry([10])
        channel_filter = InServedChannel(registry)
        message = SimpleNamespace(chat=SimpleNamespace(id=20))

        self.assertFalse(await channel_filter(message))

        registry.add(20)

        self.assertTrue(await channel_filter(message))


if __name__ == "__main__":
    unittest.main()
