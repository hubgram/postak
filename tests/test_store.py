import tempfile
import unittest

from postak.store import GLOBAL_PROMPT, InMemoryDialogStore, Message, SqliteDialogStore

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

    async def test_system_prompt_set_get_delete(self) -> None:
        store = InMemoryDialogStore()

        self.assertIsNone(await store.get_system_prompt(GLOBAL_PROMPT))
        await store.set_system_prompt(GLOBAL_PROMPT, "override")
        self.assertEqual(await store.get_system_prompt(GLOBAL_PROMPT), "override")
        await store.delete_system_prompt(GLOBAL_PROMPT)
        self.assertIsNone(await store.get_system_prompt(GLOBAL_PROMPT))


class SqliteSystemPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_system_prompt_persists_across_reopen(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            await store.set_system_prompt(GLOBAL_PROMPT, "override")
            await store.set_system_prompt((10, 20), "thread override")
            await store.close()

            reopened = SqliteDialogStore(db.name)
            await reopened.connect()
            try:
                self.assertEqual(await reopened.get_system_prompt(GLOBAL_PROMPT), "override")
                self.assertEqual(await reopened.get_system_prompt((10, 20)), "thread override")

                await reopened.set_system_prompt(GLOBAL_PROMPT, "newer")
                self.assertEqual(await reopened.get_system_prompt(GLOBAL_PROMPT), "newer")

                await reopened.delete_system_prompt(GLOBAL_PROMPT)
                self.assertIsNone(await reopened.get_system_prompt(GLOBAL_PROMPT))
            finally:
                await reopened.close()

    async def test_get_system_prompt_is_cached_including_negatives(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                self.assertIsNone(await store.get_system_prompt(GLOBAL_PROMPT))
                await store._conn.execute(
                    "INSERT INTO sysprompts (chat_id, thread_id, prompt) VALUES (0, 0, 'sneaky')"
                )
                await store._conn.commit()

                self.assertIsNone(await store.get_system_prompt(GLOBAL_PROMPT))

                await store.set_system_prompt(GLOBAL_PROMPT, "override")
                await store._conn.execute("DELETE FROM sysprompts")
                await store._conn.commit()

                self.assertEqual(await store.get_system_prompt(GLOBAL_PROMPT), "override")
            finally:
                await store.close()


if __name__ == "__main__":
    unittest.main()
