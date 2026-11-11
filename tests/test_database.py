"""Unit tests for the database module."""

import random
import string
import unittest
from time import time

from imagehash import hex_to_hash

from just_memes_telegram.modules.data import Fingerprint, Post
from just_memes_telegram.modules.database import Database
from tests.utils import TempSettingsMixin


def createRandomPost(days_old: int = 0) -> Post:
    """Create a post with random content.

    Args:
        days_old (int, optional): age of the post, in days

    Returns:
        Post: the post
    """
    post_id = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    return Post(
        url=f"https://i.redd.it/{post_id}.png",
        id=post_id,
        subreddit="memes",
        title="".join(random.choices(string.ascii_letters, k=20)),
        score=random.randint(0, 100),
        timestamp=time() - 86400 * days_old,
        video=random.choice([True, False]),
    )


def createRandomFingerprint(url: str, days_old: int = 0) -> Fingerprint:
    """Create a fingerprint with random content.

    Args:
        url (str): url of the fingerprinted post
        days_old (int, optional): age of the fingerprint, in days

    Returns:
        Fingerprint: the fingerprint
    """
    hash_str = "".join(random.choices("0123456789abcdef", k=16))
    return Fingerprint(
        caption="".join(random.choices(string.ascii_letters, k=20)),
        hash=hex_to_hash(hash_str),
        hash_str=hash_str,
        url=url,
        timestamp=time() - 86400 * days_old,
    )


class DatabaseTest(TempSettingsMixin, unittest.IsolatedAsyncioTestCase):
    """Unit tests for the Database class, on a temporary SQLite file."""

    _posts_num: int = 10

    async def asyncSetUp(self) -> None:
        """Create and connect a database in a temporary folder."""
        self.createTempSettings()
        self.database = Database(settings_path=self.settings_path)
        await self.database.connect()

    async def asyncTearDown(self) -> None:
        """Close the database and remove the temporary folder."""
        await self.database.close()
        self.removeTempSettings()

    async def _addRandomData(self, days_old: int = 0) -> tuple[list, list]:
        """Add random posts and fingerprints to the database.

        Args:
            days_old (int, optional): age of the data, in days

        Returns:
            tuple[list, list]: the added posts and fingerprints
        """
        posts = [createRandomPost(days_old) for _ in range(self._posts_num)]
        fingerprints = [createRandomFingerprint(p.url, days_old) for p in posts]
        for post, fingerprint in zip(posts, fingerprints, strict=True):
            await self.database.addData(post=post, fingerprint=fingerprint)
        return posts, fingerprints

    async def testProperties(self) -> None:
        """Test the properties and the string representation."""
        self.assertTrue(self.database.is_connected)
        self.assertRegex(self.database.sqlite_version, r"^\d+(\.\d+){2,}$")
        self.assertEqual(self.database.database_path, str(self.temp_dir / "test.db"))
        self.assertTupleEqual(await self.database.getStoredData(), (0, 0))

        self.assertEqual(repr(self.database), str(self.database))
        self.assertRegex(
            str(self.database),
            r"^Database:(\n\t· [\w ]+: .+)+$",
        )
        self.assertIn("stored posts in database: 0", await self.database.describe())

    async def testNotConnected(self) -> None:
        """Test that using a closed database raises an error."""
        await self.database.close()
        self.assertFalse(self.database.is_connected)
        with self.assertRaises(RuntimeError):
            await self.database.getOldIds()

    async def testAdd(self) -> None:
        """Test that the added data can be read back."""
        posts, fingerprints = await self._addRandomData()

        self.assertTupleEqual(
            await self.database.getStoredData(), (self._posts_num, self._posts_num)
        )
        self.assertSetEqual(await self.database.getOldIds(), {p.id for p in posts})
        self.assertSetEqual(await self.database.getOldUrls(), {p.url for p in posts})
        self.assertSetEqual(
            await self.database.getOldCaptions(), {f.caption for f in fingerprints}
        )
        self.assertSetEqual(
            await self.database.getOldHashes(), {f.hash for f in fingerprints}
        )

    async def testAddPartialData(self) -> None:
        """Test posts without id and fingerprints without hash or caption."""
        await self.database.addData(post=Post(url="https://i.redd.it/manual.png"))
        await self.database.addData(
            fingerprint=Fingerprint(caption=None, hash=None, hash_str=None, url="u")
        )

        self.assertTupleEqual(await self.database.getStoredData(), (1, 1))
        self.assertSetEqual(await self.database.getOldIds(), set())
        self.assertSetEqual(await self.database.getOldHashes(), set())
        self.assertSetEqual(await self.database.getOldCaptions(), set())

    async def testClean(self) -> None:
        """Test that only old data is removed."""
        await self._addRandomData(days_old=60)
        posts, _ = await self._addRandomData()

        removed = await self.database.clean()
        self.assertTupleEqual(removed, (self._posts_num, self._posts_num))
        self.assertTupleEqual(
            await self.database.getStoredData(), (self._posts_num, self._posts_num)
        )
        self.assertSetEqual(await self.database.getOldIds(), {p.id for p in posts})

    async def testDeleteAll(self) -> None:
        """Test that all data is removed."""
        await self._addRandomData()
        removed = await self.database.deleteAll()
        self.assertTupleEqual(removed, (self._posts_num, self._posts_num))
        self.assertTupleEqual(await self.database.getStoredData(), (0, 0))

    async def testPersistence(self) -> None:
        """Test that the data survives a reconnection."""
        posts, _ = await self._addRandomData()
        await self.database.close()

        async with Database(settings_path=self.settings_path) as database:
            self.assertSetEqual(await database.getOldIds(), {p.id for p in posts})
