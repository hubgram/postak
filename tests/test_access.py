import tempfile
import unittest
from types import SimpleNamespace

from postak.access import AccessPolicy, AccessScope, CanAnswer
from postak.store import InMemoryDialogStore, SqliteDialogStore


def message(
    *,
    user_id: int | None = 1,
    chat_id: int = 10,
    thread_id: int | None = 20,
    text: str | None = "hello",
    caption: str | None = None,
) -> SimpleNamespace:
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(
        from_user=user,
        chat=SimpleNamespace(id=chat_id),
        message_thread_id=thread_id,
        text=text,
        caption=caption,
        sender_chat=None,
    )


class AccessPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_everyone_is_default(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)

        self.assertTrue(await policy.can_answer(message(user_id=None), 10, 20))

    async def test_restricted_global_denies_anonymous_messages(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)

        await policy.restrict_everyone(AccessScope.global_())

        self.assertFalse(await policy.can_answer(message(user_id=None), 10, 20))

    async def test_admin_can_answer_when_public_is_off(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)

        await policy.restrict_everyone(AccessScope.global_())
        await policy.add_admin(7)

        self.assertTrue(await policy.can_answer(message(user_id=7), 10, 20))

    async def test_scoped_allowed_user_can_answer(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)

        await policy.restrict_everyone(AccessScope.global_())
        await policy.allow_user(7, AccessScope.thread(10, 20))

        self.assertTrue(await policy.can_answer(message(user_id=7), 10, 20))
        self.assertFalse(await policy.can_answer(message(user_id=7), 10, 21))


    async def test_anonymous_group_admin_can_manage(self) -> None:
        policy = AccessPolicy(InMemoryDialogStore())
        anonymous = message(user_id=None)
        anonymous.sender_chat = SimpleNamespace(id=10)

        self.assertTrue(await policy.can_manage(anonymous))
        self.assertFalse(await policy.can_manage(message(user_id=None)))


class CanAnswerTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_rejects_closed_thread(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)
        can_answer = CanAnswer(store, policy)

        self.assertFalse(await can_answer(message()))

    async def test_filter_rejects_unauthorized_comment(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)
        can_answer = CanAnswer(store, policy)

        await store.start((10, 20), (30, 40))
        await policy.restrict_everyone(AccessScope.global_())

        self.assertFalse(await can_answer(message(user_id=7)))

    async def test_filter_accepts_caption_only_messages(self) -> None:
        store = InMemoryDialogStore()
        can_answer = CanAnswer(store, AccessPolicy(store))

        await store.start((10, 20), (30, 40))

        self.assertEqual(
            await can_answer(message(text=None, caption="look at this")), {"thread_id": 20}
        )
        self.assertFalse(await can_answer(message(text=None)))

    async def test_filter_injects_thread_id_for_allowed_comment(self) -> None:
        store = InMemoryDialogStore()
        policy = AccessPolicy(store)
        can_answer = CanAnswer(store, policy)

        await store.start((10, 20), (30, 40))
        await policy.restrict_everyone(AccessScope.global_())
        await policy.allow_user(7, AccessScope.group(10))

        self.assertEqual(await can_answer(message(user_id=7)), {"thread_id": 20})

class SqliteAccessStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_access_rules_persist(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            await store.add_admin(1)
            await store.allow_user(2, AccessScope.thread(10, 20).key())
            await store.set_public(AccessScope.group(10).key(), False)
            await store.close()

            reopened = SqliteDialogStore(db.name)
            await reopened.connect()
            try:
                self.assertTrue(await reopened.is_admin(1))
                self.assertTrue(await reopened.is_user_allowed(2, AccessScope.thread(10, 20).key()))
                self.assertFalse(await reopened.get_public(AccessScope.group(10).key()))
            finally:
                await reopened.close()


class DialogStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_memory_store_replaces_history(self) -> None:
        store = InMemoryDialogStore()
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "old")

        await store.replace_history((10, 20), [{"role": "assistant", "content": "summary"}])

        self.assertEqual(await store.history((10, 20)), [
            {"role": "assistant", "content": "summary"}
        ])

    async def test_sqlite_store_replaces_history(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40), system="system")
                await store.add((10, 20), "user", "old")

                await store.replace_history(
                    (10, 20), [{"role": "assistant", "content": "summary"}]
                )

                self.assertEqual(await store.history((10, 20)), [
                    {"role": "assistant", "content": "summary"}
                ])
            finally:
                await store.close()

    async def test_sqlite_add_many_appends_in_order(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40))
                await store.add_many((10, 20), [
                    {"role": "user", "content": "a"},
                    {"role": "user", "content": "b"},
                ])

                self.assertEqual(await store.history((10, 20)), [
                    {"role": "user", "content": "a"},
                    {"role": "user", "content": "b"},
                ])
            finally:
                await store.close()

    async def test_sqlite_has_is_served_from_cache_after_start(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40))
                # Delete the row behind the store's back; a cached hit must still
                # answer True without touching the database.
                await store._conn.execute("DELETE FROM threads")
                await store._conn.commit()

                self.assertTrue(await store.has((10, 20)))
                self.assertFalse(await store.has((10, 99)))
            finally:
                await store.close()

    async def test_sqlite_admin_cache_follows_add_and_remove(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                await store.add_admin(1)
                await store._conn.execute("DELETE FROM access_admins")
                await store._conn.commit()
                self.assertTrue(await store.is_admin(1))

                await store.remove_admin(1)
                self.assertFalse(await store.is_admin(1))
            finally:
                await store.close()

    async def test_sqlite_get_public_is_cached_including_negatives(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                scope = AccessScope.group(10).key()
                # A "no row" result is cached, so a later direct insert is not seen.
                self.assertIsNone(await store.get_public(scope))
                await store._conn.execute(
                    "INSERT INTO access_public_scopes "
                    "(scope_kind, chat_id, thread_id, public) VALUES ('group', 10, 0, 1)"
                )
                await store._conn.commit()
                self.assertIsNone(await store.get_public(scope))

                # set_public refreshes the cache; the flag is then served from it.
                await store.set_public(scope, True)
                await store._conn.execute("DELETE FROM access_public_scopes")
                await store._conn.commit()
                self.assertTrue(await store.get_public(scope))
            finally:
                await store.close()

    async def test_sqlite_store_enables_wal_and_busy_timeout(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name)
            await store.connect()
            try:
                journal = await (await store._conn.execute("PRAGMA journal_mode")).fetchone()
                timeout = await (await store._conn.execute("PRAGMA busy_timeout")).fetchone()
                self.assertEqual(journal[0], "wal")
                self.assertEqual(timeout[0], 5000)
            finally:
                await store.close()

    async def test_memory_store_windows_history_and_keeps_system(self) -> None:
        store = InMemoryDialogStore(window=3)
        await store.start((10, 20), (30, 40), system="system")
        for index in range(5):
            await store.add((10, 20), "user", f"m{index}")

        self.assertEqual(await store.history((10, 20)), [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "m2"},
            {"role": "user", "content": "m3"},
            {"role": "user", "content": "m4"},
        ])

    async def test_sqlite_store_windows_history_and_keeps_system(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name, window=3)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40), system="system")
                for index in range(5):
                    await store.add((10, 20), "user", f"m{index}")

                self.assertEqual(await store.history((10, 20)), [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "m2"},
                    {"role": "user", "content": "m3"},
                    {"role": "user", "content": "m4"},
                ])
            finally:
                await store.close()

    async def test_sqlite_store_windows_history_without_system(self) -> None:
        with tempfile.NamedTemporaryFile() as db:
            store = SqliteDialogStore(db.name, window=3)
            await store.connect()
            try:
                await store.start((10, 20), (30, 40))
                for index in range(5):
                    await store.add((10, 20), "user", f"m{index}")

                self.assertEqual(await store.history((10, 20)), [
                    {"role": "user", "content": "m2"},
                    {"role": "user", "content": "m3"},
                    {"role": "user", "content": "m4"},
                ])
            finally:
                await store.close()


if __name__ == "__main__":
    unittest.main()
