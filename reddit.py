import praw
import ujson
import logging

from time import time
from data import Post


class Reddit:
    """
    This class handles all the connections to Reddit, including post fetching
    """

    def __init__(self) -> None:
        self._settings = {}
        self._settings_path = "settings/settings.json"
        self._loadSettings()
        self._login()

    def _isVideo(self, url) -> bool:
        return "v.redd.it" in url

    def _loadSettings(self) -> None:
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Reddit"]

    def _saveSettings(self) -> None:
        """Saves all settings to file"""
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Reddit"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _login(self) -> None:
        """Logins into Reddit app api"""
        self.reddit = praw.Reddit(
            client_id=self._settings["id"],
            client_secret=self._settings["token"],
            user_agent="PC",
        )

    def _loadPosts(self) -> list[Post]:
        """Loads posts from reddit

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

            if not submission:
                # we found nothing
                return 0

            # we want to skip selftexts and stickied submission
            if submission.selftext or submission.stickied:
                continue

            # skip galleries and videos
            if any(x in submission.url for x in [".gif", "gallery"]):
                continue

            # skip gifs and videos if flag is set
            if not self._settings["include_videos"] and self._isVideo(submission.url):
                continue

            subreddit = submission.subreddit.display_name
            title = submission.title or None
            score = submission.score or -1

            # Append the current found post to the list of posts
            posts.append(
                Post(
                    submission.url,
                    submission.id,
                    subreddit,
                    title,
                    score,
                    timestamp,
                    self._isVideo(submission.url),
                )
            )

        return sorted(posts, key=lambda x: x.score, reverse=True)

    def fetch(self) -> list[Post]:
        """
        Fetch posts from Reddit.
        Posts are returned sorted by score.
        """
        logging.info("Fetching new memes.")

        posts = self._loadPosts()

        logging.info(f"{len(posts)} posts found.")
        logging.info("Memes fetched.")

        return posts

    @property
    def subreddits(self) -> str:
        return " ".join(sorted([r.lower() for r in self._settings["subreddits"]]))

    @subreddits.setter
    def subreddits(self, subreddits):
        self._settings["subreddits"] = [r for r in subreddits]
        self._saveSettings()

    @property
    def settings(self) -> dict:
        return self._settings

    def __str__(self) -> str:
        subreddits = " ".join(self._settings["subreddits"])
        return "\n\t· ".join(
            [
                "Reddit:",
                f"requests: {self._settings['request_limit']}",
                f"subreddits: {subreddits}",
                f"videos included: {self._settings['include_videos']}",
            ]
        )
