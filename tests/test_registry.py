import unittest

from postak.registry import AdminRegistry, ChannelRegistry


class AdminRegistryTest(unittest.TestCase):
    def test_admins_start_from_constructor(self) -> None:
        registry = AdminRegistry([1, 2, 1])

        self.assertEqual(registry.admins, {1, 2})
        self.assertEqual(registry.removals, set())

    def test_add_clears_pending_removal(self) -> None:
        registry = AdminRegistry()

        registry.remove(1)
        registry.add(1)

        self.assertEqual(registry.admins, {1})
        self.assertEqual(registry.removals, set())

    def test_remove_clears_pending_add(self) -> None:
        registry = AdminRegistry([1])

        registry.remove(1)

        self.assertEqual(registry.admins, set())
        self.assertEqual(registry.removals, {1})


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

    def test_remove_drops_channel_and_its_discussion_link(self) -> None:
        registry = ChannelRegistry([10])
        registry.link_discussion(channel_id=10, discussion_group_id=20)

        removed = registry.remove(10)

        self.assertEqual(removed, 20)
        self.assertEqual(registry.channels, [])
        self.assertIsNone(registry.channel_for_discussion(20))

    def test_remove_unknown_channel_returns_none(self) -> None:
        registry = ChannelRegistry([10])

        self.assertIsNone(registry.remove(99))
        self.assertEqual(registry.channels, [10])


if __name__ == "__main__":
    unittest.main()
