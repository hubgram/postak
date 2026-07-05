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
) -> SimpleNamespace:
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(
        from_user=user,
        chat=SimpleNamespace(id=chat_id),
        message_thread_id=thread_id,
        text=text,
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


if __name__ == "__main__":
    unittest.main()
