"""Unit tests for the reddit module."""

import unittest
from time import time

import pytest

from just_memes_telegram.modules.data import Post
from just_memes_telegram.modules.reddit import Reddit
from tests.utils import REAL_SETTINGS_PATH, TempSettingsMixin


class RedditTest(TempSettingsMixin, unittest.IsolatedAsyncioTestCase):
    """Unit tests for the Reddit class."""

    def setUp(self) -> None:
        """Create the temporary settings."""
        self.createTempSettings()

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self.removeTempSettings()

    async def testProperties(self) -> None:
        """Test the properties and the string representation."""
        reddit = Reddit(settings_path=self.settings_path)
        self.assertListEqual(reddit.subreddits, ["dankmemes", "memes"])
        self.assertEqual(reddit.subreddits_str, "dankmemes memes")
        self.assertEqual(repr(reddit), str(reddit))
        self.assertRegex(str(reddit), r"^Reddit:(\n\t· [\w ]+: .+)+$")
        await reddit.close()

    @pytest.mark.credentials
    async def testFetch(self) -> None:
        """Test fetching posts with real credentials."""
        reddit = Reddit(settings_path=REAL_SETTINGS_PATH)
        posts = await reddit.fetch()
        await reddit.close()

        self.assertGreater(len(posts), 0)
        scores = [p.score for p in posts]
        self.assertListEqual(scores, sorted(scores, reverse=True))
        for post in posts:
            self.assertIsInstance(post, Post)
            self.assertIn((post.subreddit or "").lower(), reddit.subreddits)
            self.assertRegex(post.id or "", r"^[a-zA-Z0-9]+$")
            self.assertRegex(post.url, r"^https?://.*")
            self.assertLessEqual(post.timestamp, time())
            self.assertIsNone(post.path)
