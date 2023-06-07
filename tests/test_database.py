import random
import string
import unittest
from time import time

from modules.data import Fingerprint, Post
from modules.database import Database


class DatabaseTest(unittest.TestCase):
    def _createDatabase(self):
        database = Database()
        # inject test settings
        database._loadSettings()
        database._settings["database_name"] = "tests"
        database._connect()
        return database

    def _clearDatabases(self):
        database = self._createDatabase()
        database._db["posts"].drop()
        database._db["fingerprints"].drop()

    def tearDown(self):
        # just to be sure
        self._clearDatabases()

    def _createRandomPost(self, days_old: int = 0, video: bool = False):
        timestamp = time() - 86400 * days_old

        subreddit = "".join(random.sample(string.ascii_lowercase, 10))
        post_id = "".join(random.sample(string.ascii_letters + string.digits, 10))
        url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}"
        title = "".join(random.sample(string.ascii_letters, 20))
        path = f"tmp/{time()*1000}"

        return Post(
            url=url,
            id=post_id,
            subreddit=subreddit,
            title=title,
            score=random.randint(0, 100),
            timestamp=timestamp,
            video=video,
            path=path,
        )

    def _createRandomFingerprint(self, url: str, days_old: int = 0):
        caption = "".join(random.sample(string.ascii_letters, 10))
        hash = [random.randint(0, 255) for _ in range(32)]
        hash_str = "".join([hex(x)[2:] for x in hash])
        caption = "".join(random.sample(string.ascii_letters, 20))
        timestamp = time() - 86400 * days_old

        return Fingerprint(
            caption=caption,
            hash=hash,
            hash_str=hash_str,
            url=url,
            timestamp=timestamp,
        )

    def testDatabaseProperties(self):
        database = self._createDatabase()
        self.assertIsNotNone(database._client)
        self.assertIsNotNone(database._db)

        self.assertTrue(database.is_connected)
        self.assertRegex(database.mongodb_url, r"^mongodb://.*$")
        self.assertRegex(database.mongodb_version, r"^\d+(\.\d+){2,}$")

        data = database.stored_data
        self.assertIsInstance(data, tuple)
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], int)
        self.assertIsInstance(data[1], int)

        self.assertIsInstance(str(database), str)
        self.assertEqual(database.__repr__(), str(database))
        self.assertRegex(
            str(database),
            database.__class__.__name__ + ":" + r"\n\s*(·\s.*:\s.*\n\s*)+·\s.*:\s.*",
        )

    def testDatabaseAdd(self):
        database = self._createDatabase()
        self.assertTupleEqual(database.stored_data, (0, 0))

        posts_num = 10

        posts = [self._createRandomPost() for _ in range(posts_num)]
        fingerprints = [
            self._createRandomFingerprint(url=posts[x].url) for x in range(posts_num)
        ]

        for x in range(posts_num):
            database.addData(posts[x], fingerprints[x])

        self.assertTupleEqual(database.stored_data, (posts_num, posts_num))

        old_ids = {x.id for x in posts}
        self.assertSetEqual(database.getOldIds(), old_ids)

        old_urls = {x.url for x in posts}
        self.assertSetEqual(database.getOldUrls(), old_urls)

        old_captions = {x.caption for x in fingerprints}
        self.assertSetEqual(database.getOldCaptions(), old_captions)

        old_captions = {x.caption for x in fingerprints}
        self.assertSetEqual(database.getOldCaptions(), old_captions)

        self._clearDatabases()

    def testDatabaseClear(self):
        database = self._createDatabase()
        self.assertTupleEqual(database.stored_data, (0, 0))

        posts_num = 10

        old_posts = [self._createRandomPost(days_old=60) for _ in range(posts_num)]
        old_fingerprints = [
            self._createRandomFingerprint(url=old_posts[x].url, days_old=60)
            for x in range(posts_num)
        ]

        for x in range(posts_num):
            database.addData(old_posts[x], old_fingerprints[x])

        self.assertTupleEqual(database.stored_data, (posts_num, posts_num))

        posts = [self._createRandomPost() for _ in range(posts_num)]
        fingerprints = [
            self._createRandomFingerprint(url=posts[x].url) for x in range(posts_num)
        ]

        for x in range(posts_num):
            database.addData(posts[x], fingerprints[x])

        self.assertTupleEqual(database.stored_data, (posts_num * 2, posts_num * 2))

        removed = database.clean()
        self.assertTupleEqual(removed, (posts_num, posts_num))
        self.assertTupleEqual(database.stored_data, (posts_num, posts_num))

        self._clearDatabases()
