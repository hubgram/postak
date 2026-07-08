import unittest
from unittest.mock import patch

from postak.config import Settings


class SettingsTest(unittest.TestCase):
    def test_channels_and_admins_accept_comma_separated_lists(self) -> None:
        env = {
            "BOT_TOKEN": "token",
            "TARGET_CHANNEL_ID": "-1001, -1002",
            "LLM_MODEL": "gpt-4o-mini",
            "POSTAK_ADMINS": "1,2",
        }

        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.target_channel_ids, [-1001, -1002])
        self.assertEqual(settings.admins, [1, 2])

    def test_missing_required_variable_raises(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            Settings.from_env()

    def test_target_channel_id_is_optional(self) -> None:
        env = {"BOT_TOKEN": "token", "LLM_MODEL": "gpt-4o-mini", "POSTAK_ADMINS": "1"}

        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.target_channel_ids, [])


if __name__ == "__main__":
    unittest.main()
