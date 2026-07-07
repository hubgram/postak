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

    def remove(self, channel_id: int) -> int | None:
        """Remove a served channel; returns its linked discussion group id, if any."""
        if channel_id not in self._channels:
            return None
        self._channels.remove(channel_id)
        group_id = next(
            (gid for gid, cid in self._channel_of_group.items() if cid == channel_id), None
        )
        if group_id is not None:
            del self._channel_of_group[group_id]
        return group_id

    def link_discussion(self, channel_id: int, discussion_group_id: int) -> None:
        self._channel_of_group[discussion_group_id] = channel_id

    def channel_for_discussion(self, discussion_group_id: int) -> int | None:
        return self._channel_of_group.get(discussion_group_id)


class AdminRegistry:
    """Tracks startup admin grants and removals before the store is connected."""

    def __init__(self, admins: list[int] | None = None) -> None:
        self._admins: set[int] = set(admins or [])
        self._removals: set[int] = set()

    @property
    def admins(self) -> set[int]:
        return set(self._admins)

    @property
    def removals(self) -> set[int]:
        return set(self._removals)

    def add(self, user_id: int) -> None:
        self._admins.add(user_id)
        self._removals.discard(user_id)

    def remove(self, user_id: int) -> None:
        self._admins.discard(user_id)
        self._removals.add(user_id)
