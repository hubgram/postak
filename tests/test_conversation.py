import asyncio
import unittest
from types import SimpleNamespace

from postak.conversation import Conversations
from postak.store import Key


def message(chat_id: int = 10, text: str = "hello") -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id), text=text)


class RecordingConversations(Conversations):
    def __init__(self, delay: float = 0.0) -> None:
        super().__init__(generator=SimpleNamespace(), store=SimpleNamespace())
        self.processed: list[Key] = []
        self._delay = delay

    async def _generate(self, batch, key: Key) -> None:
        await asyncio.sleep(self._delay)
        self.processed.append(key)


class FailingConversations(Conversations):
    def __init__(self) -> None:
        super().__init__(generator=SimpleNamespace(), store=SimpleNamespace())

    async def _generate(self, batch, key: Key) -> None:
        raise RuntimeError("boom")


class ConversationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_idle_thread_state_is_evicted_after_processing(self) -> None:
        conversations = RecordingConversations()
        key = (10, 20)

        conversations.enqueue(message(), thread_id=20)
        task = conversations._states[key].task
        self.assertIsNotNone(task)
        await task

        self.assertEqual(conversations.processed, [key])
        self.assertNotIn(key, conversations._states)

    async def test_enqueue_recreates_evicted_thread_state(self) -> None:
        conversations = RecordingConversations()
        key = (10, 20)

        conversations.enqueue(message(), thread_id=20)
        first_task = conversations._states[key].task
        self.assertIsNotNone(first_task)
        await first_task

        conversations.enqueue(message(text="again"), thread_id=20)
        second_task = conversations._states[key].task
        self.assertIsNotNone(second_task)
        await second_task

        self.assertEqual(conversations.processed, [key, key])
        self.assertNotIn(key, conversations._states)

    async def test_drain_awaits_in_flight_generation(self) -> None:
        conversations = RecordingConversations(delay=0.02)
        key = (10, 20)

        conversations.enqueue(message(), thread_id=20)
        # The generation has not finished yet; drain must wait for it.
        self.assertEqual(conversations.processed, [])

        await conversations.drain()

        self.assertEqual(conversations.processed, [key])
        self.assertNotIn(key, conversations._states)

    async def test_drain_is_a_noop_without_active_threads(self) -> None:
        conversations = RecordingConversations()

        await conversations.drain()

        self.assertEqual(conversations.processed, [])

    async def test_failed_generation_notifies_the_user(self) -> None:
        conversations = FailingConversations()
        key = (10, 20)
        replies: list[str] = []

        async def reply(text: str, parse_mode: object = None) -> None:
            replies.append(text)

        msg = SimpleNamespace(chat=SimpleNamespace(id=10), text="hi", reply=reply)
        conversations.enqueue(msg, thread_id=20)
        await conversations._states[key].task

        self.assertEqual(len(replies), 1)
        self.assertIn("couldn't finish", replies[0])


if __name__ == "__main__":
    unittest.main()
