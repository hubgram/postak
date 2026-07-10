import aiosqlite

from postak.store.base import DEFAULT_WINDOW, AccessKey, Key, Message

# Prompts are read per reply and keyed per thread; cap the cache so it can't grow forever.
_PROMPT_CACHE_MAX = 1024


def _scope_values(scope: AccessKey) -> tuple[str, int, int]:
    kind, chat_id, thread_id = scope
    return kind, chat_id or 0, thread_id or 0


def _row_message(role: str, content: str, user_id: int | None, user_name: str | None) -> Message:
    message: Message = {"role": role, "content": content}
    if user_id is not None:
        message["user_id"] = user_id
    if user_name is not None:
        message["user_name"] = user_name
    return message


class SqliteDialogStore:
    """SQLite-backed DialogStore via aiosqlite. Durable across restarts."""

    def __init__(self, path: str, window: int = DEFAULT_WINDOW) -> None:
        self._path = path
        self._window = window
        self._db: aiosqlite.Connection | None = None
        # Positive cache of open thread keys. Thread rows are only ever inserted
        # (never deleted), so a True result is permanent and needs no invalidation.
        self._known_threads: set[Key] = set()
        # Cache of public-scope flags (including "no row" as None), read on every
        # comment. set_public is the sole writer, so it keeps the cache current.
        self._public_cache: dict[tuple[str, int, int], bool | None] = {}
        self._admin_cache: dict[int, bool] = {}
        self._allowed_cache: dict[tuple[int, tuple[str, int, int]], bool] = {}
        self._prompt_cache: dict[Key, str | None] = {}

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteDialogStore is not connected; call connect() first")
        return self._db

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        # WAL lets readers and the writer proceed concurrently; NORMAL trades a
        # sliver of durability for far fewer fsyncs; busy_timeout avoids spurious
        # "database is locked" errors when a write briefly contends.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
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
                content TEXT NOT NULL,
                user_id INTEGER,
                user_name TEXT
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
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                discussion_group_id INTEGER NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS sysprompts (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                PRIMARY KEY (chat_id, thread_id)
            );
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._known_threads.clear()
        self._public_cache.clear()
        self._admin_cache.clear()
        self._allowed_cache.clear()
        self._prompt_cache.clear()

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
        self._known_threads.add(key)

    async def channel_message(self, key: Key) -> Key | None:
        cursor = await self._conn.execute(
            "SELECT channel_chat_id, channel_post_id FROM threads "
            "WHERE chat_id = ? AND thread_id = ?",
            key,
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    async def has(self, key: Key) -> bool:
        if key in self._known_threads:
            return True
        cursor = await self._conn.execute(
            "SELECT 1 FROM threads WHERE chat_id = ? AND thread_id = ?", key
        )
        exists = await cursor.fetchone() is not None
        if exists:
            self._known_threads.add(key)
        return exists

    async def add(self, key: Key, role: str, content: str) -> None:
        await self._conn.execute(
            "INSERT INTO messages (chat_id, thread_id, role, content) VALUES (?, ?, ?, ?)",
            (*key, role, content),
        )
        await self._conn.commit()

    async def add_many(self, key: Key, messages: list[Message]) -> None:
        await self._conn.executemany(
            "INSERT INTO messages (chat_id, thread_id, role, content, user_id, user_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (*key, m["role"], m["content"], m.get("user_id"), m.get("user_name"))
                for m in messages
            ],
        )
        await self._conn.commit()

    async def history(self, key: Key) -> list[Message]:
        # Read only the last `window` rows via the (chat_id, thread_id, id) index
        # instead of the whole thread, then keep the leading system prompt even
        # after it has scrolled out of the window (mirrors window_messages).
        cursor = await self._conn.execute(
            "SELECT id, role, content, user_id, user_name FROM messages "
            "WHERE chat_id = ? AND thread_id = ? ORDER BY id DESC LIMIT ?",
            (*key, self._window),
        )
        tail = list(await cursor.fetchall())[::-1]
        messages: list[Message] = [_row_message(*row[1:]) for row in tail]
        if len(tail) < self._window:  # the tail already holds the whole thread
            return messages

        cursor = await self._conn.execute(
            "SELECT id, role, content FROM messages WHERE chat_id = ? AND thread_id = ? "
            "ORDER BY id LIMIT 1",
            key,
        )
        first = await cursor.fetchone()
        if first is not None and first[1] == "system" and (not tail or first[0] < tail[0][0]):
            messages.insert(0, {"role": first[1], "content": first[2]})
        return messages

    async def replace_history(self, key: Key, messages: list[Message]) -> None:
        await self._conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND thread_id = ?",
            key,
        )
        await self._conn.executemany(
            "INSERT INTO messages (chat_id, thread_id, role, content, user_id, user_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (*key, m["role"], m["content"], m.get("user_id"), m.get("user_name"))
                for m in messages
            ],
        )
        await self._conn.commit()

    async def add_admin(self, user_id: int) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO access_admins (user_id) VALUES (?)",
            (user_id,),
        )
        await self._conn.commit()
        self._admin_cache[user_id] = True

    async def remove_admin(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM access_admins WHERE user_id = ?", (user_id,))
        await self._conn.commit()
        self._admin_cache[user_id] = False

    async def is_admin(self, user_id: int) -> bool:
        if (cached := self._admin_cache.get(user_id)) is not None:
            return cached
        cursor = await self._conn.execute(
            "SELECT 1 FROM access_admins WHERE user_id = ?",
            (user_id,),
        )
        result = await cursor.fetchone() is not None
        self._admin_cache[user_id] = result
        return result

    async def admins(self) -> list[int]:
        cursor = await self._conn.execute("SELECT user_id FROM access_admins ORDER BY user_id")
        return [row[0] for row in await cursor.fetchall()]

    async def allow_user(self, user_id: int, scope: AccessKey) -> None:
        values = _scope_values(scope)
        await self._conn.execute(
            "INSERT OR IGNORE INTO access_allowed_users "
            "(user_id, scope_kind, chat_id, thread_id) VALUES (?, ?, ?, ?)",
            (user_id, *values),
        )
        await self._conn.commit()
        self._allowed_cache[(user_id, values)] = True

    async def revoke_user(self, user_id: int, scope: AccessKey) -> None:
        values = _scope_values(scope)
        await self._conn.execute(
            "DELETE FROM access_allowed_users "
            "WHERE user_id = ? AND scope_kind = ? AND chat_id = ? AND thread_id = ?",
            (user_id, *values),
        )
        await self._conn.commit()
        self._allowed_cache[(user_id, values)] = False

    async def is_user_allowed(self, user_id: int, scope: AccessKey) -> bool:
        values = _scope_values(scope)
        if (cached := self._allowed_cache.get((user_id, values))) is not None:
            return cached
        cursor = await self._conn.execute(
            "SELECT 1 FROM access_allowed_users "
            "WHERE user_id = ? AND scope_kind = ? AND chat_id = ? AND thread_id = ?",
            (user_id, *values),
        )
        result = await cursor.fetchone() is not None
        self._allowed_cache[(user_id, values)] = result
        return result

    async def set_public(self, scope: AccessKey, public: bool) -> None:
        values = _scope_values(scope)
        await self._conn.execute(
            "INSERT INTO access_public_scopes (scope_kind, chat_id, thread_id, public) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(scope_kind, chat_id, thread_id) DO UPDATE SET public = excluded.public",
            (*values, int(public)),
        )
        await self._conn.commit()
        self._public_cache[values] = public

    async def get_public(self, scope: AccessKey) -> bool | None:
        values = _scope_values(scope)
        if values in self._public_cache:
            return self._public_cache[values]
        cursor = await self._conn.execute(
            "SELECT public FROM access_public_scopes "
            "WHERE scope_kind = ? AND chat_id = ? AND thread_id = ?",
            values,
        )
        row = await cursor.fetchone()
        result = bool(row[0]) if row else None
        self._public_cache[values] = result
        return result

    async def allowed_users(self) -> list[tuple[int, AccessKey]]:
        cursor = await self._conn.execute(
            "SELECT user_id, scope_kind, chat_id, thread_id FROM access_allowed_users "
            "ORDER BY user_id, scope_kind, chat_id, thread_id"
        )
        return [
            (user_id, (kind, chat_id or None, thread_id or None))
            for user_id, kind, chat_id, thread_id in await cursor.fetchall()
        ]

    async def public_scopes(self) -> list[tuple[AccessKey, bool]]:
        cursor = await self._conn.execute(
            "SELECT scope_kind, chat_id, thread_id, public FROM access_public_scopes "
            "ORDER BY scope_kind, chat_id, thread_id"
        )
        return [
            ((kind, chat_id or None, thread_id or None), bool(public))
            for kind, chat_id, thread_id, public in await cursor.fetchall()
        ]

    async def clear_chat(self, chat_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM access_allowed_users WHERE chat_id = ?", (chat_id,)
        )
        await self._conn.execute(
            "DELETE FROM access_public_scopes WHERE chat_id = ?", (chat_id,)
        )
        await self._conn.commit()
        self._allowed_cache.clear()
        self._public_cache.clear()

    def _cache_prompt(self, key: Key, value: str | None) -> None:
        if key not in self._prompt_cache and len(self._prompt_cache) >= _PROMPT_CACHE_MAX:
            self._prompt_cache.clear()
        self._prompt_cache[key] = value

    async def get_system_prompt(self, key: Key) -> str | None:
        if key in self._prompt_cache:
            return self._prompt_cache[key]
        cursor = await self._conn.execute(
            "SELECT prompt FROM sysprompts WHERE chat_id = ? AND thread_id = ?", key
        )
        row = await cursor.fetchone()
        result = row[0] if row else None
        self._cache_prompt(key, result)
        return result

    async def set_system_prompt(self, key: Key, prompt: str) -> None:
        await self._conn.execute(
            "INSERT INTO sysprompts (chat_id, thread_id, prompt) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, thread_id) DO UPDATE SET prompt = excluded.prompt",
            (*key, prompt),
        )
        await self._conn.commit()
        self._cache_prompt(key, prompt)

    async def delete_system_prompt(self, key: Key) -> None:
        await self._conn.execute(
            "DELETE FROM sysprompts WHERE chat_id = ? AND thread_id = ?", key
        )
        await self._conn.commit()
        self._cache_prompt(key, None)

    async def add_channel(self, channel_id: int, discussion_group_id: int) -> None:
        await self._conn.execute(
            "INSERT INTO channels (channel_id, discussion_group_id) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET "
            "discussion_group_id = excluded.discussion_group_id",
            (channel_id, discussion_group_id),
        )
        await self._conn.commit()

    async def remove_channel(self, chat_id: int) -> tuple[int, int] | None:
        cursor = await self._conn.execute(
            "SELECT channel_id, discussion_group_id FROM channels "
            "WHERE channel_id = ? OR discussion_group_id = ?",
            (chat_id, chat_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await self._conn.execute("DELETE FROM channels WHERE channel_id = ?", (row[0],))
        await self._conn.commit()
        return (row[0], row[1])

    async def channel_links(self) -> list[tuple[int, int]]:
        cursor = await self._conn.execute(
            "SELECT channel_id, discussion_group_id FROM channels ORDER BY channel_id"
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]
