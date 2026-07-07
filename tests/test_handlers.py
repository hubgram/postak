import unittest
from types import SimpleNamespace

from postak.handlers import answer_discussion


async def _record(sink: list, value) -> None:
    sink.append(value)


class AnswerDiscussionTest(unittest.IsolatedAsyncioTestCase):
    async def test_reacts_and_enqueues(self) -> None:
        reactions: list = []
        enqueued: list = []
        message = SimpleNamespace(react=None)
        message.react = lambda r: _record(reactions, r)
        conversations = SimpleNamespace(enqueue=lambda m, t: enqueued.append((m, t)))

        await answer_discussion(message, conversations, thread_id=20)

        self.assertEqual(len(reactions), 1)
        self.assertEqual(reactions[0][0].emoji, "👀")
        self.assertEqual(enqueued, [(message, 20)])


if __name__ == "__main__":
    unittest.main()
