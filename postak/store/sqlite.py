import aiosqlite

from postak.store.base import DEFAULT_WINDOW, Key, Message, window_messages


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
            CREATE TABLE IF NOT EXISTS pending (
                chat_id INTEGER NOT NULL,
                channel_post_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, channel_post_id)
            );
            CREATE TABLE IF NOT EXISTS threads (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                channel_chat_id INTEGER NOT NULL,
                channel_post_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, thread_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (chat_id, thread_id, id);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def mark_pending(self, key: Key) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO pending (chat_id, channel_post_id) VALUES (?, ?)", key
        )
        await self._conn.commit()

    async def take_pending(self, key: Key) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM pending WHERE chat_id = ? AND channel_post_id = ?", key
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def start(self, key: Key, channel_post: Key, system: str | None = None) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO threads (chat_id, thread_id, channel_chat_id, channel_post_id) "
            "VALUES (?, ?, ?, ?)",
            (*key, *channel_post),
        )
        if system:
            await self._conn.execute(
                "INSERT INTO messages (chat_id, thread_id, role, content) VALUES (?, ?, ?, ?)",
                (*key, "system", system),
            )
        await self._conn.commit()

    async def channel_message(self, key: Key) -> Key | None:
        cursor = await self._conn.execute(
            "SELECT channel_chat_id, channel_post_id FROM threads "
            "WHERE chat_id = ? AND thread_id = ?",
            key,
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    async def has(self, key: Key) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM threads WHERE chat_id = ? AND thread_id = ?", key
        )
        return await cursor.fetchone() is not None

    async def add(self, key: Key, role: str, content: str) -> None:
        await self._conn.execute(
            "INSERT INTO messages (chat_id, thread_id, role, content) VALUES (?, ?, ?, ?)",
            (*key, role, content),
        )
        await self._conn.commit()

    async def history(self, key: Key) -> list[Message]:
        cursor = await self._conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? AND thread_id = ? ORDER BY id",
            key,
        )
        rows = await cursor.fetchall()
        messages: list[Message] = [{"role": role, "content": content} for role, content in rows]
        return window_messages(messages, self._window)
