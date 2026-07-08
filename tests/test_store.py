import tempfile
import unittest

from postak.store import InMemoryDialogStore, Message, SqliteDialogStore

MESSAGES: list[Message] = [
    {"role": "user", "content": "hi", "user_id": 7, "user_name": "Ada Lovelace"},
    {"role": "user", "content": "anon", "user_name": "My Group"},
    {"role": "user", "content": "plain"},
]


class SqliteDialogStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_round_trips_through_history(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40), system="sys")
                await store.add_many((10, 20), MESSAGES)
                await store.add((10, 20), "assistant", "hello")

                history = await store.history((10, 20))

                self.assertEqual(history[0], {"role": "system", "content": "sys"})
                self.assertEqual(history[1:4], MESSAGES)
                self.assertEqual(history[4], {"role": "assistant", "content": "hello"})
            finally:
                await store.close()

    async def test_replace_history_preserves_identity(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40))
                await store.add((10, 20), "assistant", "stale")

                await store.replace_history((10, 20), MESSAGES)

                self.assertEqual(await store.history((10, 20)), MESSAGES)
            finally:
                await store.close()


class InMemoryDialogStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_round_trips_through_history(self) -> None:
        store = InMemoryDialogStore()
        await store.start((10, 20), (30, 40))

        await store.add_many((10, 20), MESSAGES)

        self.assertEqual(await store.history((10, 20)), MESSAGES)


if __name__ == "__main__":
    unittest.main()
