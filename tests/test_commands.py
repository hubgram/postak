import unittest
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import patch

from aiogram.enums import ChatMemberStatus, ChatType

from postak import commands as commands_module
from postak.commands import POSTAK_HELP, POSTAK_USAGE, _reply, postak_admin
from postak.registry import ChannelRegistry
from postak.store import GLOBAL_PROMPT, InMemoryDialogStore


async def drain_stream(message, tokens) -> str:
    text = "".join([token async for token in tokens])
    message.streams.append(text)
    return text


class FakeAccessPolicy:
    def __init__(self, *, can_manage: bool = True) -> None:
        self._can_manage = can_manage
        self.cleared_chats: list[int] = []

    async def can_manage(self, message) -> bool:
        return self._can_manage

    async def admins(self) -> list[int]:
        return [1, 2]

    async def allowed_users(self):
        return [(7, ("group", 10, None))]

    async def public_scopes(self):
        return [(("global", None, None), True)]

    async def clear_chat(self, chat_id: int) -> None:
        self.cleared_chats.append(chat_id)


class FakePostak:
    def __init__(self) -> None:
        self.model = "old"
        self.title_prompt = "title prompt"
        self.system_prompt = "default system"
        self.generator = FakeGenerator("digest text")
        self.channel_registry = ChannelRegistry()

    def set_model(self, model: str) -> "FakePostak":
        self.model = model
        return self


class FakeGenerator:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages = []

    async def tokens(self, messages) -> AsyncIterator[str]:
        self.messages = messages
        yield self.response


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id: int = 10,
        thread_id: int | None = 20,
        reply_to_message=None,
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.message_thread_id = thread_id
        self.reply_to_message = reply_to_message
        self.bot = FakeBot()
        self.replies: list[str] = []
        self.streams: list[str] = []

    async def reply(self, text: str, parse_mode=None) -> None:
        self.replies.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.edits = []
        self.deletes = []
        self.chats: dict[int, SimpleNamespace] = {}
        self.memberships: dict[int, SimpleNamespace] = {}

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None) -> None:
        self.edits.append((text, chat_id, message_id, parse_mode))

    async def delete_message(self, chat_id, message_id) -> None:
        self.deletes.append((chat_id, message_id))

    async def get_chat(self, chat_id):
        return self.chats[chat_id]

    async def get_chat_member(self, chat_id, user_id):
        return self.memberships[chat_id]


class PostakAdminHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_postak_command_replies_when_user_is_not_admin(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="digest")

        await postak_admin(
            message,
            command,
            FakeAccessPolicy(can_manage=False),
            pt,
            InMemoryDialogStore(),
        )

        self.assertEqual(message.replies, ["You are not a Postak admin."])

    async def test_empty_postak_command_shows_usage(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args=None)

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, [POSTAK_USAGE])

    async def test_help_command_lists_subcommands(self) -> None:
        message = FakeMessage()
        command = SimpleNamespace(args="help")

        await postak_admin(
            message, command, FakeAccessPolicy(), FakePostak(), InMemoryDialogStore()
        )

        self.assertEqual(message.replies, [POSTAK_HELP])

    async def test_model_set_command_changes_runtime_model(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="model set next")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(pt.model, "next")
        self.assertEqual(message.replies, ["Model changed to next."])

    async def test_sysprompt_shows_the_default_when_unset(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="sysprompt")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["Default system prompt:\ndefault system"])

    async def test_sysprompt_set_stores_and_shows_the_override(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        command = SimpleNamespace(args="sysprompt Be terse.  Answer in Arabic.")

        await postak_admin(message, command, FakeAccessPolicy(), FakePostak(), store)
        show = SimpleNamespace(args="sysprompt")
        await postak_admin(message, show, FakeAccessPolicy(), FakePostak(), store)

        self.assertEqual(
            await store.get_system_prompt(GLOBAL_PROMPT), "Be terse.  Answer in Arabic."
        )
        self.assertEqual(
            message.replies,
            [
                "System prompt updated. New conversations will use it.",
                "Be terse.  Answer in Arabic.",
            ],
        )

    async def test_sysprompt_delete_resets_to_default(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        await store.set_system_prompt(GLOBAL_PROMPT, "override")
        command = SimpleNamespace(args="sysprompt delete")

        await postak_admin(message, command, FakeAccessPolicy(), FakePostak(), store)

        self.assertIsNone(await store.get_system_prompt(GLOBAL_PROMPT))
        self.assertEqual(message.replies, ["System prompt reset to default."])

    async def test_admin_list_reports_admin_ids(self) -> None:
        message = FakeMessage()
        command = SimpleNamespace(args="admin list")

        await postak_admin(
            message, command, FakeAccessPolicy(), FakePostak(), InMemoryDialogStore()
        )

        self.assertEqual(message.replies, ["Admins: 1, 2"])

    async def test_access_list_reports_rules(self) -> None:
        message = FakeMessage()
        command = SimpleNamespace(args="access list")

        await postak_admin(
            message, command, FakeAccessPolicy(), FakePostak(), InMemoryDialogStore()
        )

        self.assertEqual(message.replies, ["public global: on\nuser 7: group 10"])

    async def test_add_command_registers_current_chat_from_linked_group(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(id=20, type=ChatType.SUPERGROUP, linked_chat_id=10)
        message.bot.memberships[10] = SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR, can_post_messages=True
        )
        pt = FakePostak()
        store = InMemoryDialogStore()
        command = SimpleNamespace(args="add")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(pt.channel_registry.channel_for_discussion(20), 10)
        self.assertEqual(await store.channel_links(), [(10, 20)])
        self.assertEqual(message.replies, ["Added channel 10 linked to group 20."])

    async def test_add_command_accepts_an_explicit_channel_id(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[10] = SimpleNamespace(id=10, type=ChatType.CHANNEL, linked_chat_id=20)
        message.bot.memberships[10] = SimpleNamespace(status=ChatMemberStatus.CREATOR)
        pt = FakePostak()
        command = SimpleNamespace(args="add 10")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(pt.channel_registry.channel_for_discussion(20), 10)
        self.assertEqual(message.replies, ["Added channel 10 linked to group 20."])

    async def test_add_command_reports_a_chat_with_no_discussion_link(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(
            id=20, type=ChatType.SUPERGROUP, linked_chat_id=None
        )
        pt = FakePostak()
        command = SimpleNamespace(args="add")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(
            message.replies,
            [
                "This chat isn't linked to a channel/discussion group on Telegram yet. "
                "Link them in the channel's Discussion settings first."
            ],
        )

    async def test_add_command_reports_missing_post_permission(self) -> None:
        message = FakeMessage(chat_id=20)
        message.bot.chats[20] = SimpleNamespace(id=20, type=ChatType.SUPERGROUP, linked_chat_id=10)
        message.bot.memberships[10] = SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR, can_post_messages=False
        )
        pt = FakePostak()
        command = SimpleNamespace(args="add")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertIsNone(pt.channel_registry.channel_for_discussion(20))
        self.assertEqual(
            message.replies,
            [
                "Postak isn't an admin with 'Post Messages' rights in channel 10. "
                "Grant it that permission, then try again."
            ],
        )

    async def test_remove_command_drops_channel_and_scoped_access(self) -> None:
        message = FakeMessage(chat_id=20)
        pt = FakePostak()
        store = InMemoryDialogStore()
        await store.add_channel(10, 20)
        pt.channel_registry.add(10)
        pt.channel_registry.link_discussion(10, 20)
        access_policy = FakeAccessPolicy()
        command = SimpleNamespace(args="remove")

        await postak_admin(message, command, access_policy, pt, store)

        self.assertIsNone(pt.channel_registry.channel_for_discussion(20))
        self.assertEqual(await store.channel_links(), [])
        self.assertEqual(sorted(access_policy.cleared_chats), [10, 20])
        self.assertEqual(message.replies, ["Removed channel 10 (group 20)."])

    async def test_remove_command_accepts_an_explicit_channel_id(self) -> None:
        message = FakeMessage(chat_id=99)
        pt = FakePostak()
        store = InMemoryDialogStore()
        await store.add_channel(10, 20)
        pt.channel_registry.add(10)
        pt.channel_registry.link_discussion(10, 20)
        command = SimpleNamespace(args="remove 10")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(await store.channel_links(), [])
        self.assertEqual(message.replies, ["Removed channel 10 (group 20)."])

    async def test_remove_command_reports_unknown_chat(self) -> None:
        message = FakeMessage(chat_id=20)
        pt = FakePostak()
        command = SimpleNamespace(args="remove")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["No channel registered for 20."])

    async def test_long_reply_is_split_into_telegram_sized_chunks(self) -> None:
        message = FakeMessage()

        await _reply(message, "x" * 5000)

        self.assertEqual([len(reply) for reply in message.replies], [4096, 904])

    async def test_model_get_command_reports_current_model(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="model get")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["Current model: old."])

    async def test_digest_command_replies_with_thread_digest(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        command = SimpleNamespace(args="digest")
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "first")
        await store.add((10, 20), "assistant", "second")

        with patch.object(commands_module, "stream_tokens", drain_stream):
            await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.streams, ["digest text"])
        self.assertEqual(pt.generator.messages[-2:], [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ])

    async def test_digest_command_requires_postak_thread(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="digest")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["This thread is not a Postak conversation."])

    async def test_compress_command_replaces_thread_history_with_summary(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        pt.generator = FakeGenerator("short summary")
        command = SimpleNamespace(args="compress")
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "first")
        await store.add((10, 20), "assistant", "second")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(await store.history((10, 20)), [
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": "Conversation summary so far:\nshort summary",
            },
        ])
        self.assertEqual(message.replies, ["Compressed thread history."])

    async def test_compress_command_requires_postak_thread(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="compress")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["This thread is not a Postak conversation."])

    async def test_settitle_command_edits_channel_post_title(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        command = SimpleNamespace(args="settitle Better title")
        await store.start((10, 20), (30, 40), system="system")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.bot.edits, [("Better title", 30, 40, None)])
        self.assertEqual(message.replies, ["Title changed to Better title."])

    async def test_settitle_command_uses_replied_thread_when_thread_id_is_missing(self) -> None:
        message = FakeMessage(thread_id=None, reply_to_message=SimpleNamespace(message_id=20))
        store = InMemoryDialogStore()
        pt = FakePostak()
        command = SimpleNamespace(args="settitle Better title")
        await store.start((10, 20), (30, 40), system="system")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.bot.edits, [("Better title", 30, 40, None)])
        self.assertEqual(message.replies, ["Title changed to Better title."])

    async def test_settitle_command_requires_title_text(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="settitle")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["Usage: /postak settitle <text>"])

    async def test_title_command_regenerates_channel_post_title(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        pt.generator = FakeGenerator("Fresh title\nignored body")
        command = SimpleNamespace(args="title")
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "first")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.bot.edits, [("Fresh title", 30, 40, None)])
        self.assertEqual(message.replies, ["Title changed to Fresh title."])

    async def test_title_command_requires_postak_thread(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="title")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["This thread is not a Postak conversation."])

    async def test_delete_command_deletes_replied_message(self) -> None:
        reply = SimpleNamespace(message_id=99)
        message = FakeMessage(reply_to_message=reply)
        pt = FakePostak()
        command = SimpleNamespace(args="delete")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.bot.deletes, [(10, 99)])
        self.assertEqual(message.replies, ["Deleted message."])

    async def test_delete_command_requires_reply(self) -> None:
        message = FakeMessage()
        pt = FakePostak()
        command = SimpleNamespace(args="delete")

        await postak_admin(message, command, FakeAccessPolicy(), pt, InMemoryDialogStore())

        self.assertEqual(message.replies, ["Reply to a message with /postak delete."])

    async def test_regenerate_command_answers_from_latest_user_message(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        pt.generator = FakeGenerator("fresh answer")
        command = SimpleNamespace(args="regenerate")
        await store.start((10, 20), (30, 40), system="system")
        await store.add((10, 20), "user", "question")
        await store.add((10, 20), "assistant", "old answer")

        with patch.object(commands_module, "stream_tokens", drain_stream):
            await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(pt.generator.messages[-1], {"role": "user", "content": "question"})
        self.assertEqual(message.streams, ["fresh answer"])
        self.assertEqual((await store.history((10, 20)))[-1], {
            "role": "assistant",
            "content": "fresh answer",
        })

    async def test_regenerate_command_requires_user_message(self) -> None:
        message = FakeMessage()
        store = InMemoryDialogStore()
        pt = FakePostak()
        command = SimpleNamespace(args="regenerate")
        await store.start((10, 20), (30, 40), system="system")

        await postak_admin(message, command, FakeAccessPolicy(), pt, store)

        self.assertEqual(message.replies, ["No user message to regenerate from."])


if __name__ == "__main__":
    unittest.main()
