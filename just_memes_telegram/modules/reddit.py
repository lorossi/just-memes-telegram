"""Class handling the Reddit interface."""

from pathlib import Path
from time import time
from typing import Any

import asyncpraw
from loguru import logger

from just_memes_telegram import __version__
from just_memes_telegram.modules.common import DEFAULT_SETTINGS_PATH, loadSettings
from just_memes_telegram.modules.data import Post
from just_memes_telegram.modules.mediadownloader import isVideoUrl

USER_AGENT = f"python:just-memes-telegram:v{__version__}"


class Reddit:
    """Class handling the Reddit interface."""

    _settings: dict[str, Any]
    _reddit: asyncpraw.Reddit

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        """Initialize the reddit object.

        Args:
            settings_path (str | Path, optional): path of the settings file.
        """
        self._settings = loadSettings(settings_path, "Reddit")
        self._reddit = asyncpraw.Reddit(
            client_id=self._settings["id"],
            client_secret=self._settings["token"],
            user_agent=USER_AGENT,
        )

    async def close(self) -> None:
        """Close the connection to Reddit."""
        await self._reddit.close()

    async def _loadPosts(self) -> list[Post]:
        """Load posts from Reddit.

        Returns:
            list[Post]: loaded posts, sorted by score (highest first)
        """
        posts: list[Post] = []

        subreddits = await self._reddit.subreddit("+".join(self.subreddits))
        async for submission in subreddits.hot(limit=self._settings["request_limit"]):
            # skip selftexts, stickied submission and galleries
            if submission.selftext or submission.stickied:
                continue
            if "gallery" in submission.url:
                continue

            posts.append(
                Post(
                    url=submission.url,
                    id=submission.id,
                    subreddit=submission.subreddit.display_name,
                    title=submission.title or None,
                    score=submission.score,
                    timestamp=time(),
                    video=isVideoUrl(submission.url),
                )
            )

        return sorted(posts, key=lambda x: x.score, reverse=True)

    async def fetch(self) -> list[Post]:
        """Fetch posts asynchronously from Reddit.

        Returns:
            list[Post]: list of posts
        """
        logger.info("Fetching new memes.")
        posts = await self._loadPosts()
        logger.info("{} posts loaded.", len(posts))
        return posts

    @property
    def subreddits(self) -> list[str]:
        """The sorted list of subreddits."""
        return sorted(r.lower() for r in self._settings["subreddits"] if r)

    @property
    def subreddits_str(self) -> str:
        """The list of subreddits as a string."""
        return " ".join(self.subreddits)

    def __repr__(self) -> str:
        """Return string representation of object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"requests: {self._settings['request_limit']}",
                f"subreddits: {self.subreddits_str}",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of object."""
        return self.__repr__()
