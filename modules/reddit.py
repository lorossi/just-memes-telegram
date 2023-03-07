"""Class handling reddit interface."""
import logging
from cmath import log
from time import time

import praw
import ujson

from .data import Post


class Reddit:
    """Class handling reddit interface."""

    def __init__(self) -> None:
        """Initialize the reddit object."""
        self._settings = {}
        self._settings_path = "settings/settings.json"
        self._loadSettings()
        self._login()

    def _isVideo(self, url) -> bool:
        return any(x in url for x in [".gif", "v.redd.it"])

    def _isGif(self, url) -> bool:
        return ".gif" in url

    def _loadSettings(self) -> None:
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Reddit"]

    def _saveSettings(self) -> None:
        """Save all settings to file."""
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Reddit"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _login(self) -> None:
        """Login into Reddit app api."""
        self.reddit = praw.Reddit(
            client_id=self._settings["id"],
            client_secret=self._settings["token"],
            user_agent="PC",
        )

    def _loadPosts(self) -> list[Post]:
        """Load posts from Reddit.

        Returns:
            [int]: [number of posts loaded]
        """
        # Loads new posts
        timestamp = time()
        posts = []

        subreddit_list = "+".join(self._settings["subreddits"])
        for submission in self.reddit.subreddit(subreddit_list).hot(
            limit=self._settings["request_limit"]
        ):
            # we want to skip selftexts and stickied submission
            if submission.selftext or submission.stickied:
                continue

            # skip galleries
            if "gallery" in submission.url:
                continue

            subreddit = submission.subreddit.display_name
            title = submission.title or None
            score = submission.score or -1
            is_video = self._isVideo(submission.url)

            # Append the current found post to the list of posts
            posts.append(
                Post(
                    url=submission.url,
                    id=submission.id,
                    subreddit=subreddit,
                    title=title,
                    score=score,
                    timestamp=timestamp,
                    video=is_video,
                )
            )

        # no posts have been found
        if not posts:
            return None

        return sorted(posts, key=lambda x: x.score, reverse=True)

    def fetch(self) -> list[Post]:
        """Fetch posts from Reddit.

        Returns:
            list[Post]: list of posts
        """
        logging.info("Fetching new memes.")

        posts = self._loadPosts()

        logging.info(f"{len(posts)} posts found.")

        return posts

    @property
    def subreddits(self) -> str:
        """Get list of subreddits."""
        return " ".join(sorted([r.lower() for r in self._settings["subreddits"]]))

    @subreddits.setter
    def subreddits(self, subreddits: list[str]):
        self._settings["subreddits"] = [r for r in subreddits]
        self._saveSettings()

    @property
    def settings(self) -> dict:
        """Get settings."""
        return self._settings

    def __str__(self) -> str:
        """Get string representation of object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"requests: {self._settings['request_limit']}",
                f"subreddits: {self.subreddits}",
            ]
        )
