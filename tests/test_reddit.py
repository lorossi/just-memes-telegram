import os
import shutil
import unittest
from time import time

from modules.reddit import Reddit
from modules.data import Post


class RedditTest(unittest.TestCase):
    _subreddits: list[str] = [
        "aww",
        "dankmemes",
        "funny",
        "me_irl",
        "memes",
    ]
    _requests_limit: int = 100
    _settings_path: str = "settings/settings.json"
    _temp_settings_path: str = "tests/temp_settings.json"

    def setUp(self) -> None:
        shutil.copy(self._settings_path, self._temp_settings_path)

    def tearDown(self) -> None:
        os.remove(self._temp_settings_path)

    def _createReddit(self):
        reddit = Reddit()
        reddit._settings_path = self._temp_settings_path
        reddit._settings["subreddits"] = self._subreddits
        reddit._settings["request_limit"] = self._requests_limit
        return reddit

    def testCreation(self):
        reddit = Reddit()
        self.assertIsInstance(reddit, Reddit)

    def testProperties(self):
        reddit = self._createReddit()

        subreddits = sorted(r.lower() for r in self._subreddits)
        subreddits_str = " ".join(subreddits)
        self.assertListEqual(reddit.subreddits, subreddits)
        self.assertEqual(reddit.subreddits_str, subreddits_str)

        self.assertIsInstance(str(reddit), str)
        self.assertEqual(reddit.__repr__(), str(reddit))
        self.assertRegex(
            str(reddit),
            reddit.__class__.__name__ + ":" + r"\n\s*(·\s.*:\s.*\n\s*)+·\s.*:\s.*",
        )


class TestAsyncReddit(unittest.IsolatedAsyncioTestCase, RedditTest):
    async def testFetch(self):
        reddit = self._createReddit()
        posts = await reddit.fetch()
        self.assertIsInstance(posts, list)
        self.assertGreater(len(posts), 0)
        self.assertLessEqual(len(posts), self._requests_limit)

        for post in posts:
            self.assertIsInstance(post, Post)
            self.assertIsInstance(post.url, str)
            self.assertIsInstance(post.id, str)
            self.assertIsInstance(post.subreddit, str)
            self.assertIsInstance(post.title, str)
            self.assertIsInstance(post.score, int)
            self.assertIsInstance(post.timestamp, float)
            self.assertIsInstance(post.video, bool)
            self.assertIsNone(post.path)

            self.assertIn(post.subreddit.lower(), reddit.subreddits)
            self.assertRegex(post.id, r"^[a-zA-Z0-9]+$")
            self.assertGreaterEqual(post.score, 0)
            self.assertLessEqual(post.timestamp, time())
            self.assertRegex(post.url, r"^https?://.*")
