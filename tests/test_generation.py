import unittest

from postak.generation import OpenAIGenerator, prepare_messages
from postak.store import Message


class OpenAIGeneratorTest(unittest.TestCase):
    def test_set_model_changes_future_model(self) -> None:
        generator = OpenAIGenerator(model="first", api_key="test-key")

        generator.set_model("second")

        self.assertEqual(generator.model, "second")


class PrepareMessagesTest(unittest.TestCase):
    def test_identity_becomes_sanitized_name_with_id(self) -> None:
        messages: list[Message] = [
            {"role": "user", "content": "hi", "user_id": 7, "user_name": "Ada Lovelace"}
        ]

        self.assertEqual(
            prepare_messages(messages),
            [{"role": "user", "content": "hi", "name": "Ada_Lovelace-7"}],
        )

    def test_forbidden_characters_are_replaced(self) -> None:
        messages: list[Message] = [
            {"role": "user", "content": "hi", "user_id": 7, "user_name": "a<b|c\\d/e> f"}
        ]

        self.assertEqual(
            prepare_messages(messages),
            [{"role": "user", "content": "hi", "name": "a_b_c_d_e_f-7"}],
        )

    def test_anonymous_name_has_no_id_suffix(self) -> None:
        messages: list[Message] = [{"role": "user", "content": "hi", "user_name": "My Group"}]

        self.assertEqual(
            prepare_messages(messages),
            [{"role": "user", "content": "hi", "name": "My_Group"}],
        )

    def test_long_names_keep_the_id_suffix(self) -> None:
        messages: list[Message] = [
            {"role": "user", "content": "hi", "user_id": 123456, "user_name": "x" * 100}
        ]

        self.assertEqual(
            prepare_messages(messages),
            [{"role": "user", "content": "hi", "name": "x" * 57 + "-123456"}],
        )

    def test_plain_messages_pass_through_without_mutation(self) -> None:
        messages: list[Message] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_id": 7, "user_name": "Ada"},
            {"role": "assistant", "content": "hello"},
        ]

        prepared = prepare_messages(messages)

        self.assertEqual(prepared[0], {"role": "system", "content": "sys"})
        self.assertEqual(prepared[2], {"role": "assistant", "content": "hello"})
        self.assertEqual(
            messages[1], {"role": "user", "content": "hi", "user_id": 7, "user_name": "Ada"}
        )


if __name__ == "__main__":
    unittest.main()
