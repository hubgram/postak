import aiosqlite

from postak.store.base import DEFAULT_WINDOW, AccessKey, Key, Message, window_messages


def _scope_values(scope: AccessKey) -> tuple[str, int, int]:
    kind, chat_id, thread_id = scope
    return kind, chat_id or 0, thread_id or 0


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
            CREATE TABLE IF NOT EXISTS access_admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS access_allowed_users (
                user_id INTEGER NOT NULL,
                scope_kind TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, scope_kind, chat_id, thread_id)
            );
            CREATE TABLE IF NOT EXISTS access_public_scopes (
                scope_kind TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                public INTEGER NOT NULL,
                PRIMARY KEY (scope_kind, chat_id, thread_id)
            );
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

    async def add_admin(self, user_id: int) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO access_admins (user_id) VALUES (?)",
            (user_id,),
        )
        await self._conn.commit()

    async def remove_admin(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM access_admins WHERE user_id = ?", (user_id,))
        await self._conn.commit()

    async def is_admin(self, user_id: int) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM access_admins WHERE user_id = ?",
            (user_id,),
        )
        return await cursor.fetchone() is not None

    async def allow_user(self, user_id: int, scope: AccessKey) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO access_allowed_users "
            "(user_id, scope_kind, chat_id, thread_id) VALUES (?, ?, ?, ?)",
            (user_id, *_scope_values(scope)),
        )
        await self._conn.commit()

    async def revoke_user(self, user_id: int, scope: AccessKey) -> None:
        await self._conn.execute(
            "DELETE FROM access_allowed_users "
            "WHERE user_id = ? AND scope_kind = ? AND chat_id = ? AND thread_id = ?",
            (user_id, *_scope_values(scope)),
        )
        await self._conn.commit()

    async def is_user_allowed(self, user_id: int, scope: AccessKey) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM access_allowed_users "
            "WHERE user_id = ? AND scope_kind = ? AND chat_id = ? AND thread_id = ?",
            (user_id, *_scope_values(scope)),
        )
        return await cursor.fetchone() is not None

    async def set_public(self, scope: AccessKey, public: bool) -> None:
        await self._conn.execute(
            "INSERT INTO access_public_scopes (scope_kind, chat_id, thread_id, public) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(scope_kind, chat_id, thread_id) DO UPDATE SET public = excluded.public",
            (*_scope_values(scope), int(public)),
        )
        await self._conn.commit()

    async def get_public(self, scope: AccessKey) -> bool | None:
        cursor = await self._conn.execute(
            "SELECT public FROM access_public_scopes "
            "WHERE scope_kind = ? AND chat_id = ? AND thread_id = ?",
            _scope_values(scope),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else None
