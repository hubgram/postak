"""Small registries for Postak runtime configuration."""


class ChannelRegistry:
    """Tracks served channels and their linked discussion groups."""

    def __init__(self, channels: list[int] | None = None) -> None:
        self._channels: list[int] = []
        self._channel_of_group: dict[int, int] = {}
        for channel_id in channels or []:
            self.add(channel_id)

    @property
    def channels(self) -> list[int]:
        return list(self._channels)

    def add(self, channel_id: int) -> None:
        if channel_id not in self._channels:
            self._channels.append(channel_id)

    def link_discussion(self, channel_id: int, discussion_group_id: int) -> None:
        self._channel_of_group[discussion_group_id] = channel_id

    def channel_for_discussion(self, discussion_group_id: int) -> int | None:
        return self._channel_of_group.get(discussion_group_id)
