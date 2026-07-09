import unittest
from types import SimpleNamespace

from aiogram.enums import ChatMemberStatus, ChatType

from postak.config import (
    NEW_CONVERSATION_CREATOR_TEMPLATE,
    NEW_CONVERSATION_GREETINGS,
    NEW_MESSAGE,
)
from postak.handlers import answer_discussion, new, new_from_group, new_from_unlinked_group
from postak.registry import ChannelRegistry
from postak.store import InMemoryDialogStore


def tagged(name: str, user_id: int) -> list[str]:
    mention = f"[{name}](tg://user?id={user_id})"
    messages = []
    for greeting in NEW_CONVERSATION_GREETINGS:
        tagged_greeting = NEW_CONVERSATION_CREATOR_TEMPLATE.format(
            user=mention, greeting=greeting
        )
        messages.append(f"{NEW_MESSAGE}\n\n{tagged_greeting}")
    return messages


def anonymous() -> list[str]:
    return [f"{NEW_MESSAGE}\n\n{greeting}" for greeting in NEW_CONVERSATION_GREETINGS]


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
    def __init__(self, *, can_manage: bool = True, can_use: bool = False) -> None:
        self._can_manage = can_manage
        self._can_use = can_use

    async def can_manage(self, message) -> bool:
        return self._can_manage

    async def can_use(self, message, chat_id) -> bool:
        return self._can_use


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.chats: dict[int, SimpleNamespace] = {}
        self.memberships: dict[int, SimpleNamespace] = {}
        self.sent: list[tuple[int, str]] = []
        self.deleted: list[tuple[int, int]] = []

    async def get_chat(self, chat_id):
        return self.chats[chat_id]

    async def get_chat_member(self, chat_id, user_id):
        return self.memberships[chat_id]

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=len(self.sent))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id: int = 20,
        message_id: int = 99,
        sender_chat_id: int | None = None,
        user_id: int | None = 1,
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id
        self.bot = FakeBot()
        self.replies: list[str] = []
        self.sender_chat = (
            SimpleNamespace(id=sender_chat_id) if sender_chat_id is not None else None
        )
        self.from_user = (
            SimpleNamespace(id=user_id, full_name="Test User") if user_id is not None else None
        )

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
        self.assertEqual(message.bot.deleted, [])

    async def test_admin_registers_and_starts_conversation(self) -> None:
        message = FakeMessage(chat_id=20, message_id=55)
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
        self.assertIn(message.bot.sent[0], [(10, text) for text in tagged("Test User", 1)])
        self.assertEqual(message.replies, ["Added channel 10 linked to group 20."])
        self.assertEqual(message.bot.deleted, [(20, 55)])

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
        self.assertEqual(message.bot.deleted, [])

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
        self.assertEqual(message.bot.deleted, [])


class NewTest(unittest.IsolatedAsyncioTestCase):
    async def test_starts_conversation_and_deletes_the_command(self) -> None:
        message = FakeMessage(chat_id=30, message_id=7, user_id=None)
        store = InMemoryDialogStore()

        await new(message, message.bot, store)

        self.assertIn(message.bot.sent[0], [(30, text) for text in anonymous()])
        self.assertEqual(message.bot.deleted, [(30, 7)])


class NewFromGroupTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_starts_conversation_and_deletes_the_command(self) -> None:
        message = FakeMessage(chat_id=20, message_id=8, sender_chat_id=20, user_id=None)
        store = InMemoryDialogStore()

        await new_from_group(
            message, message.bot, store, target_channel_id=10, access_policy=FakeAccessPolicy()
        )

        self.assertIn(message.bot.sent[0], [(10, text) for text in anonymous()])
        self.assertEqual(message.bot.deleted, [(20, 8)])

    async def test_non_admin_is_ignored_when_group_is_not_public(self) -> None:
        message = FakeMessage(chat_id=20, user_id=7)
        message.bot.memberships[20] = SimpleNamespace(status=ChatMemberStatus.MEMBER)
        store = InMemoryDialogStore()

        await new_from_group(
            message,
            message.bot,
            store,
            target_channel_id=10,
            access_policy=FakeAccessPolicy(can_use=False),
        )

        self.assertEqual(message.bot.sent, [])
        self.assertEqual(message.bot.deleted, [])

    async def test_non_admin_starts_conversation_when_group_is_public(self) -> None:
        message = FakeMessage(chat_id=20, message_id=8, user_id=7)
        message.bot.memberships[20] = SimpleNamespace(status=ChatMemberStatus.MEMBER)
        store = InMemoryDialogStore()

        await new_from_group(
            message,
            message.bot,
            store,
            target_channel_id=10,
            access_policy=FakeAccessPolicy(can_use=True),
        )

        self.assertIn(message.bot.sent[0], [(10, text) for text in tagged("Test User", 7)])
        self.assertEqual(message.bot.deleted, [(20, 8)])

    async def test_creator_name_is_escaped_for_markdown(self) -> None:
        message = FakeMessage(chat_id=20, message_id=8, user_id=7)
        message.from_user.full_name = "Ada. Lovelace!"
        message.bot.memberships[20] = SimpleNamespace(status=ChatMemberStatus.MEMBER)
        store = InMemoryDialogStore()

        await new_from_group(
            message,
            message.bot,
            store,
            target_channel_id=10,
            access_policy=FakeAccessPolicy(can_use=True),
        )

        self.assertIn(
            message.bot.sent[0],
            [(10, text) for text in tagged(r"Ada\. Lovelace\!", 7)],
        )


if __name__ == "__main__":
    unittest.main()
