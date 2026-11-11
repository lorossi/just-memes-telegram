"""Class handling all the database operations."""

import sqlite3
from pathlib import Path
from time import time
from types import TracebackType
from typing import Any, Self

import aiosqlite
from imagehash import ImageHash, hex_to_hash
from loguru import logger

from just_memes_telegram.modules.common import DEFAULT_SETTINGS_PATH, loadSettings
from just_memes_telegram.modules.data import Fingerprint, Post

MEMORY_DATABASE = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT,
    url TEXT NOT NULL,
    subreddit TEXT,
    title TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL,
    video INTEGER NOT NULL DEFAULT 0,
    path TEXT
);
CREATE INDEX IF NOT EXISTS posts_timestamp ON posts (timestamp);

CREATE TABLE IF NOT EXISTS fingerprints (
    caption TEXT,
    hash_str TEXT,
    url TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS fingerprints_timestamp ON fingerprints (timestamp);
"""

_INSERT_POST = """
INSERT INTO posts (id, url, subreddit, title, score, timestamp, video, path)
VALUES (:id, :url, :subreddit, :title, :score, :timestamp, :video, :path)
"""

_INSERT_FINGERPRINT = """
INSERT INTO fingerprints (caption, hash_str, url, timestamp)
VALUES (:caption, :hash_str, :url, :timestamp)
"""


class Database:
    """Class to interface to the SQLite database.

    The connection must be opened with `connect` (or by using the object as an
    async context manager) before any other operation.
    """

    _settings: dict[str, Any]
    _connection: aiosqlite.Connection | None

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        """Initialize the database object.

        Args:
            settings_path (str | Path, optional): path of the settings file.
        """
        self._settings = loadSettings(settings_path, "Database")
        self._connection = None

        if self.database_path != MEMORY_DATABASE:
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    async def connect(self) -> None:
        """Open the connection to the database and create the tables."""
        if self._connection is not None:
            return

        logger.info("Connecting to database {}.", self.database_path)
        self._connection = await aiosqlite.connect(self.database_path)
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()

    async def close(self) -> None:
        """Close the connection to the database."""
        if self._connection is None:
            return

        await self._connection.close()
        self._connection = None

    async def __aenter__(self) -> Self:
        """Connect to the database when entering the context.

        Returns:
            Self: the connected database
        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the database when leaving the context."""
        await self.close()

    @property
    def _db(self) -> aiosqlite.Connection:
        """The open connection.

        Raises:
            RuntimeError: if the database is not connected
        """
        if self._connection is None:
            msg = "The database is not connected. Call connect() first."
            raise RuntimeError(msg)
        return self._connection

    async def _fetchColumn(self, query: str) -> list[Any]:
        """Run a query and return the first column of every row.

        Args:
            query (str): the query to run

        Returns:
            list[Any]: values of the first column
        """
        async with self._db.execute(query) as cursor:
            return [row[0] for row in await cursor.fetchall()]

    async def _count(self, table: str) -> int:
        """Count the rows of a table.

        Args:
            table (str): either "posts" or "fingerprints"

        Returns:
            int: number of rows
        """
        query = {
            "posts": "SELECT COUNT(*) FROM posts",
            "fingerprints": "SELECT COUNT(*) FROM fingerprints",
        }[table]
        (count,) = await self._fetchColumn(query)
        return count

    async def getOldIds(self) -> set[str]:
        """Load the ids of old posts, either discarded or posted.

        Returns:
            set[str]: ids of old posts
        """
        return set(await self._fetchColumn("SELECT id FROM posts WHERE id IS NOT NULL"))

    async def getOldUrls(self) -> set[str]:
        """Load the urls of old posts, either discarded or posted.

        Returns:
            set[str]: urls of old posts
        """
        return set(await self._fetchColumn("SELECT url FROM posts"))

    async def getOldHashes(self) -> set[ImageHash]:
        """Load the hashes of old posts, either discarded or posted.

        Returns:
            set[ImageHash]: hashes of old posts
        """
        hashes = await self._fetchColumn(
            "SELECT hash_str FROM fingerprints WHERE hash_str IS NOT NULL"
        )
        return {hex_to_hash(h) for h in hashes}

    async def getOldCaptions(self) -> set[str]:
        """Load the captions of old posts, either discarded or posted.

        Returns:
            set[str]: captions of old posts
        """
        return set(
            await self._fetchColumn(
                "SELECT caption FROM fingerprints WHERE caption IS NOT NULL"
            )
        )

    async def addData(
        self, post: Post | None = None, fingerprint: Fingerprint | None = None
    ) -> None:
        """Add a Post and/or a Fingerprint to the database.

        Args:
            post (Post, optional): Post object
            fingerprint (Fingerprint, optional): Fingerprint object
        """
        if post is not None:
            await self._db.execute(_INSERT_POST, post.serialize())
        if fingerprint is not None:
            await self._db.execute(_INSERT_FINGERPRINT, fingerprint.serialize())
        await self._db.commit()

    async def clean(self) -> tuple[int, int]:
        """Remove documents older than the maximum number of days.

        Returns:
            tuple[int, int]: number of removed posts and fingerprints
        """
        max_seconds = self._settings["max_days"] * 24 * 60 * 60
        earliest_time = time() - max_seconds

        posts = await self._db.execute(
            "DELETE FROM posts WHERE timestamp < ?", (earliest_time,)
        )
        fingerprints = await self._db.execute(
            "DELETE FROM fingerprints WHERE timestamp < ?", (earliest_time,)
        )
        await self._db.commit()
        return posts.rowcount, fingerprints.rowcount

    async def deleteAll(self) -> tuple[int, int]:
        """Delete all documents from the database.

        Returns:
            tuple[int, int]: number of deleted posts and fingerprints
        """
        posts = await self._db.execute("DELETE FROM posts")
        fingerprints = await self._db.execute("DELETE FROM fingerprints")
        await self._db.commit()
        return posts.rowcount, fingerprints.rowcount

    async def getStoredData(self) -> tuple[int, int]:
        """Return the number of stored posts and fingerprints.

        Returns:
            tuple[int, int]: stored posts and stored fingerprints
        """
        return await self._count("posts"), await self._count("fingerprints")

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the database is open."""
        return self._connection is not None

    @property
    def database_path(self) -> str:
        """The path of the database file."""
        return self._settings["database_path"]

    @property
    def sqlite_version(self) -> str:
        """The version of the SQLite library."""
        return sqlite3.sqlite_version

    async def describe(self) -> str:
        """Return a description of the database, including the stored data.

        Returns:
            str: the description
        """
        lines = [repr(self)]
        if self.is_connected:
            posts, fingerprints = await self.getStoredData()
            lines.extend(
                [
                    f"stored posts in database: {posts}",
                    f"stored fingerprints in database: {fingerprints}",
                ]
            )
        return "\n\t· ".join(lines)

    def __repr__(self) -> str:
        """Return string representation of the database object."""
        status = "connected" if self.is_connected else "not connected"
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"status: {status}",
                f"SQLite version: {self.sqlite_version}",
                f"database path: {self.database_path}",
                f"max days: {self._settings['max_days']}",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of the database object."""
        return self.__repr__()
