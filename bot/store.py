class DialogStore:
    """In-memory chat dialogs keyed by discussion thread id (reset on restart)."""

    def __init__(self) -> None:
        self._dialogs: dict[int, list[dict[str, str]]] = {}

    def start(self, thread_id: int, system: str | None = None) -> None:
        """Begin a new dialog for a thread, optionally seeded with a system prompt."""
        self._dialogs[thread_id] = [{"role": "system", "content": system}] if system else []

    def has(self, thread_id: int) -> bool:
        return thread_id in self._dialogs

    def add(self, thread_id: int, role: str, content: str) -> None:
        self._dialogs[thread_id].append({"role": role, "content": content})

    def messages(self, thread_id: int) -> list[dict[str, str]]:
        return self._dialogs[thread_id]
