import unittest
from types import SimpleNamespace

from aiogram.enums import ChatMemberStatus, ChatType

from postak.config import NEW_MESSAGE
from postak.handlers import answer_discussion, new, new_from_group, new_from_unlinked_group
from postak.registry import ChannelRegistry
from postak.store import InMemoryDialogStore


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


class FakeAccessPolicy:
    def __init__(self, *, can_manage: bool = True) -> None:
        self._can_manage = can_manage

    async def can_manage(self, message) -> bool:
        return self._can_manage


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.chats: dict[int, SimpleNamespace] = {}
        self.memberships: dict[int, SimpleNamespace] = {}
        self.sent: list[tuple[int, str]] = []

    async def get_chat(self, chat_id):
        return self.chats[chat_id]

    async def get_chat_member(self, chat_id, user_id):
        return self.memberships[chat_id]

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=len(self.sent))


class FakeMessage:
    def __init__(
        self, *, chat_id: int = 20, sender_chat_id: int | None = None, user_id: int | None = 1
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.bot = FakeBot()
        self.replies: list[str] = []
        self.sender_chat = (
            SimpleNamespace(id=sender_chat_id) if sender_chat_id is not None else None
        )
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None

    async def reply(self, text: str, parse_mode=None) -> None:
        self.replies.append(text)


class NewFromUnlinkedGroupTest(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_is_refused(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        registry = ChannelRegistry()

        await new_from_unlinked_group(
            message, message.bot, store, registry, FakeAccessPolicy(can_manage=False)
        )

        self.assertEqual(message.replies, ["You are not a Postak admin."])
        self.assertEqual(registry.channels, [])

    async def test_admin_registers_and_starts_conversation(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(
            id=20, type=ChatType.SUPERGROUP, linked_chat_id=10
        )
        message.bot.memberships[10] = SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR, can_post_messages=True
        )
        store = InMemoryDialogStore()
        registry = ChannelRegistry()

        await new_from_unlinked_group(message, message.bot, store, registry, FakeAccessPolicy())

        self.assertEqual(registry.channel_for_discussion(20), 10)
        self.assertEqual(await store.channel_links(), [(10, 20)])
        self.assertEqual(message.bot.sent, [(10, NEW_MESSAGE)])
        self.assertEqual(message.replies, ["Added channel 10 linked to group 20."])

    async def test_admin_in_unlinked_group_reports_and_does_not_start(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(
            id=20, type=ChatType.SUPERGROUP, linked_chat_id=None
        )
        store = InMemoryDialogStore()
        registry = ChannelRegistry()

        await new_from_unlinked_group(message, message.bot, store, registry, FakeAccessPolicy())

        self.assertEqual(message.bot.sent, [])
        self.assertEqual(registry.channels, [])
        self.assertEqual(
            message.replies,
            [
                "This chat isn't linked to a channel/discussion group on Telegram yet. "
                "Link them in the channel's Discussion settings first."
            ],
        )

    async def test_admin_without_post_permission_reports_and_does_not_start(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(
            id=20, type=ChatType.SUPERGROUP, linked_chat_id=10
        )
        message.bot.memberships[10] = SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR, can_post_messages=False
        )
        store = InMemoryDialogStore()
        registry = ChannelRegistry()

        await new_from_unlinked_group(message, message.bot, store, registry, FakeAccessPolicy())

        self.assertEqual(message.bot.sent, [])
        self.assertEqual(registry.channels, [])
        self.assertEqual(
            message.replies,
            [
                "Postak isn't an admin with 'Post Messages' rights in channel 10. "
                "Grant it that permission, then try again."
            ],
        )


class NewTest(unittest.IsolatedAsyncioTestCase):
    async def test_starts_conversation(self) -> None:
        message = FakeMessage(chat_id=30, user_id=None)
        store = InMemoryDialogStore()

        await new(message, message.bot, store)

        self.assertEqual(message.bot.sent, [(30, NEW_MESSAGE)])


class NewFromGroupTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_starts_conversation(self) -> None:
        message = FakeMessage(chat_id=20, sender_chat_id=20, user_id=None)
        store = InMemoryDialogStore()

        await new_from_group(message, message.bot, store, target_channel_id=10)

        self.assertEqual(message.bot.sent, [(10, NEW_MESSAGE)])

    async def test_non_admin_is_silently_ignored(self) -> None:
        message = FakeMessage(chat_id=20, user_id=7)
        message.bot.memberships[20] = SimpleNamespace(status=ChatMemberStatus.MEMBER)
        store = InMemoryDialogStore()

        await new_from_group(message, message.bot, store, target_channel_id=10)

        self.assertEqual(message.bot.sent, [])


if __name__ == "__main__":
    unittest.main()
