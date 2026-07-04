import unittest

from postak.registry import ChannelRegistry


class ChannelRegistryTest(unittest.TestCase):
    def test_channels_are_deduplicated(self) -> None:
        registry = ChannelRegistry([1, 2, 1])

        registry.add(2)
        registry.add(3)

        self.assertEqual(registry.channels, [1, 2, 3])

    def test_channel_for_linked_discussion_group(self) -> None:
        registry = ChannelRegistry([10])

        registry.link_discussion(channel_id=10, discussion_group_id=20)

        self.assertEqual(registry.channel_for_discussion(20), 10)
        self.assertIsNone(registry.channel_for_discussion(30))


if __name__ == "__main__":
    unittest.main()
