import aiosqlite

from bot.store.base import DEFAULT_WINDOW, Message, window_messages


class SqliteDialogStore:
    """SQLite-backed DialogStore via aiosqlite. Durable across restarts."""

    def __init__(self, path: str, window: int = DEFAULT_WINDOW) -> None:
        self._path = path
        self._window = window
        self._db: aiosqlite.Connection | None = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteDialogStore is not connected; call connect() first")
        return self._db

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending (channel_post_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS threads (
                thread_id INTEGER PRIMARY KEY,
                channel_post_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, id);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def mark_pending(self, channel_post_id: int) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO pending (channel_post_id) VALUES (?)", (channel_post_id,)
        )
        await self._conn.commit()

    async def take_pending(self, channel_post_id: int) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM pending WHERE channel_post_id = ?", (channel_post_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def start(self, thread_id: int, channel_post_id: int, system: str | None = None) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO threads (thread_id, channel_post_id) VALUES (?, ?)",
            (thread_id, channel_post_id),
        )
        if system:
            await self._conn.execute(
                "INSERT INTO messages (thread_id, role, content) VALUES (?, 'system', ?)",
                (thread_id, system),
            )
        await self._conn.commit()

    async def channel_message(self, thread_id: int) -> int | None:
        cursor = await self._conn.execute(
            "SELECT channel_post_id FROM threads WHERE thread_id = ?", (thread_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def has(self, thread_id: int) -> bool:
        cursor = await self._conn.execute("SELECT 1 FROM threads WHERE thread_id = ?", (thread_id,))
        return await cursor.fetchone() is not None

    async def add(self, thread_id: int, role: str, content: str) -> None:
        await self._conn.execute(
            "INSERT INTO messages (thread_id, role, content) VALUES (?, ?, ?)",
            (thread_id, role, content),
        )
        await self._conn.commit()

    async def history(self, thread_id: int) -> list[Message]:
        cursor = await self._conn.execute(
            "SELECT role, content FROM messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )
        rows = await cursor.fetchall()
        messages: list[Message] = [{"role": role, "content": content} for role, content in rows]
        return window_messages(messages, self._window)
