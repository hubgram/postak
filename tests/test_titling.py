import unittest

from postak.titling import build_title_messages


class BuildTitleMessagesTest(unittest.TestCase):
    def test_preserves_the_conversation_system_prompt_and_uses_the_title_prompt(self) -> None:
        messages = build_title_messages(
            [
                {"role": "system", "content": "custom system"},
                {"role": "user", "content": "hello"},
            ],
            "custom title instruction",
        )

        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "custom system\n\ncustom title instruction"},
                {"role": "user", "content": "hello"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
