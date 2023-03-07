"""Main bot script."""

import os
import sys
import pytz
import ujson
import logging
import requests

from datetime import time, datetime, timedelta
from telegram import  constants
from telegram.ext import CommandHandler, CallbackContext, Application, Defaults

from modules.data import Post
from modules.reddit import Reddit
from modules.database import Database
from modules.fingerprinter import Fingerprinter
from modules.mediadownloader import MediaDownloader


class Telegram:
    """Class that handles all the Telegram stuff.

    BOTFATHER commands description
    start - view start message
    reset - reloads the bot
    stop - stops the bot
    status - show some infos about the bot
    nextpost - show at what time the next post is
    queue - view queue and add image(s) url(s) to queue
    cleanqueue - cleans queue
    """

    def __init__(self):
        """Initialize the bot. Settings are automatically loaded."""
        self._version = "2.1.4"  # current bot version
        self._settings_path = "settings/settings.json"
        self._settings = []
        self._queue = []
        self._send_memes_job = None
        self._preload_memes_job = None

        self._loadSettings()

    def _loadSettings(self) -> None:
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Telegram"]

    def _saveSettings(self) -> None:
        """Save settings to file."""
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Telegram"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _escapeMarkdown(self, raw: str) -> str:
        """Replace markdown delimiters with escaped ones.

        Args:
            raw (str): Un escaped string

        Returns:
            str: Markdown escaped string
        """
        to_replace = ["_", "*", "[", "`"]
        for replace in to_replace:
            raw = raw.replace(replace, f"\\{replace}")
        return raw

    def _getSecondsBetweenPosts(self) -> int:
        """Return number of seconds between consecutive posts.

        Returns:
            int
        """
        return int(24 * 60 * 60 / self._settings["posts_per_day"])

    def _calculateNextPost(self, until_preload: int = None) -> tuple[int, str]:
        """Calculate seconds until next post and its timestamp.

        Args:
            until_preload (int, optional): Seconds until next preload. \
                If None, it's recalculated.

        Returns:
            tuple[int, str]: seconds until next post and its timestamp
        """
        if not until_preload:
            until_preload, _ = self._calculatePreload()

        # convert preload into timedelta
        next_preload_time = timedelta(seconds=until_preload)
        # convert preload time into timedelta
        preload_time = timedelta(seconds=self._settings["preload_time"])
        # remove seconds and microseconds from now
        now = datetime.now().replace(microsecond=0)
        # seconds until next post
        seconds_until = (next_preload_time + preload_time).seconds
        # calculate next post timestamp
        next_post = now + next_preload_time + preload_time

        return seconds_until, next_post.isoformat(sep=" ")

    def _calculatePreload(self) -> tuple[int, str]:
        """Calculate seconds until next preload and its timestamp.

        Returns:
            tuple[int, str]: seconds until next preload and its timestamp
        """
        # convert preload time into timedelta
        preload_time = timedelta(seconds=self._settings["preload_time"])
        # convert first post into timedelta
        # convert second between posts into timedelta
        seconds_between = timedelta(seconds=self._getSecondsBetweenPosts())
        # convert start delay into timedelta
        seconds_delay = timedelta(seconds=self._settings["start_delay"])
        # remove seconds and microseconds from now
        now = datetime.now().replace(microsecond=0)
        # starting time
        next_preload = (
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            + seconds_delay
            - preload_time
        )
        # loop until it's in the future
        while next_preload <= now:
            next_preload += seconds_between

        return (next_preload - now).seconds, next_preload.isoformat(sep=" ")

    def _getNextTimestamps(self) -> tuple[str, str]:
        """Return timestamps for next post and next preload.

        Returns:
            tuple[str, str]: next post and next preload timestamps
        """
        return (
            x.next_t.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
            for x in [
                self._send_memes_job,
                self._preload_memes_job,
            ]
        )

    def _isAdmin(self, chat_id: str) -> bool:
        """Check if user is admin.

        Args:
            chat_id (str): chat id

        Returns:
            bool
        """
        return chat_id in self._settings["admins"]

    def _getFileSize(self, url: str) -> float:
        """Get file size (in MB) by the file's url.

        Args:
            url (str): file url

        Returns:
            float
        """
        try:
            s = requests.get(url, stream=True, allow_redirects=True).headers[
                "Content-length"
            ]
            return int(s) / (1024 * 1024)
        except Exception as e:
            logging.error(f"Error while getting file size: {e}")
            return -1

    def _filterOldPosts(self, posts: list) -> list:
        """Remove old posts from the list.

        Args:
            posts (list): list of new posts

        Returns:
            list
        """
        # load old ids, hashes and urls
        old_ids = self._database.getOldIds()
        old_urls = self._database.getOldUrls()
        # filter images that match old ids or old urls
        return [p for p in posts if p.id not in old_ids and p.url not in old_urls]

    def _checkGifSize(self, url: str) -> bool:
        """Check the size of a gif.

        Args:
            url (str): url of the gif

        Returns:
            bool: False if the gif is too big
        """
        size = self._getFileSize(url)

        if size > self._settings["max_gif_size"]:
            logging.warning(f"Gif size is too big: {size}MB")
            return False

        if size <= 0:
            logging.warning(f"Skipping. Cannot get file size. Error code: {size}")
            return False

        return True

    def _postHashValid(self, old_hashes: set[int], fingerprint_hash: int) -> bool:
        """Check if the post hash is valid (and as such it has been posted before).

        Args:
            old_hashes (set[int]): list of old hashed
            fingerprint_hash (int): hash to check

        Returns:
            bool
        """
        if any(
            abs(fingerprint_hash - x) < self._settings["hash_threshold"]
            for x in old_hashes
        ):
            return False

        return True

    def _postTextValid(self, text: str) -> bool:
        """Check if the post text is valid \
            (and as such it does not contain blocked words).

        Args:
            text (str): text of the post

        Returns:
            bool
        """
        if not text:
            return True

        if any(t in text for t in self._settings["words_to_skip"]):
            return False

        return True

    def _setMemesRoutineInterval(self) -> None:
        """Create routine to send memes."""
        until_preload, _ = self._calculatePreload()
        until_first, _ = self._calculateNextPost(until_preload)
        seconds_between = self._getSecondsBetweenPosts()
        # we remove already started jobs from the schedule

        # first take care of send memes job
        # remove the old job, if already set
        if self._send_memes_job:
            self._send_memes_job.schedule_removal()

        # set new routine
        self._send_memes_job = self._jobqueue.run_repeating(
            self._botSendmemeRoutine,
            interval=seconds_between,
            first=until_first,
            name="send_memes",
        )

        # then take care of preload memes job
        # remove the old job, if already set
        if self._preload_memes_job:
            self._preload_memes_job.schedule_removal()

        # set new routine
        self._preload_memes_job = self._jobqueue.run_repeating(
            self._botPreloadmemeRoutine,
            interval=seconds_between,
            first=until_preload,
            name="preload_memes",
        )

    async def _botStartupRoutine(self, context: CallbackContext) -> None:
        """Send a message to admins when the bot is started."""
        logging.info("Starting startup routine...")

        message = "*Bot started!*"
        for chat_id in self._settings["admins"]:
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )

        logging.info("Startup routine completed.")

    def _botCleanRoutine(self, _: CallbackContext) -> None:
        """Routine that handles the removal of old posts."""
        logging.info("Clear database routine begins.")

        posts, fingerprints = self._database.clean()
        logging.info(
            f"Removed {posts} post and {fingerprints} fingerprint documents "
            "of old data removed from database."
        )

        logging.info("Clearing temp folder")
        self._downloader.cleanTempFolder()

        logging.info("Clear database routine completed.")

    def _botPreloadmemeRoutine(self, _: CallbackContext) -> None:
        """Routine that preloads memes and puts them into queue."""
        logging.info("Preload memes routine begins.")
        # load url from reddit

        if len(self._queue) > 0:
            logging.info(
                f"Post already in queue: url: {self._queue[-1].url}, "
                f"path: {self._queue[-1].path}."
            )
            logging.info("Preload memes routine completed.")
            return

        # no urls on queue, fetching a new one
        posts = self._reddit.fetch()
        to_check = self._filterOldPosts(posts)
        # load old hashes for reference. It has to be loaded BEFORE the loop
        #   because the new post fingerprint will be added into the database
        #   as soon as it's created
        old_hashes = self._database.getOldHashes()

        logging.info(f"Looking for a post between {len(to_check)} filtered posts.")

        for post in to_check:
            logging.info(f"Checking post with url {post.url}.")
            # this post can get approved or rejected
            # either way, it should not be scanned again
            self._database.addData(post=post)

            # check if title contains anything not permitted
            if any(s in post.title for s in self._settings["words_to_skip"]):
                logging.info(f"Skipping. Title contains skippable words: {post.title}")
                # update database
                continue

            # check if file is too big
            if ".gif" in post.url:
                if not self._checkGifSize(post.url):
                    continue

            # first of all, download the media
            post_path, preview_path = self._downloader.downloadMedia(post.url)
            # no path = the download failed, continue
            if not post_path:
                continue

            # fingerprint the post
            fingerprint = self._fingerprinter.fingerprint(
                path=preview_path, url=post.url
            )

            # sometimes images cannot be fingerprinted.
            #   In that case, try the next image.
            if not fingerprint:
                continue

            # save the path of the file
            post.setPath(post_path)
            # update the database with post and fingerprint
            self._database.addData(fingerprint=fingerprint)

            # check if the new post is too similar to an older one
            if not self._postHashValid(old_hashes, fingerprint.hash):
                logging.warning(f"Skipping. Hash is too similar: {fingerprint.hash}")
                continue

            # check if caption contains anything not permitted
            if self._settings["ocr"] and not self._postTextValid(fingerprint.caption):
                logging.warning(
                    f"Skipping. Contains forbidden word: {fingerprint.caption}"
                )
                continue

            # a post has been found
            # adds the photo to bot queue so it can be used later
            self._queue.append(post)
            break

        logging.info(
            f"Valid post added to queue. Url: {self._queue[-1].url}, "
            f"path: {self._queue[-1].path}."
        )

        logging.info("Preload memes routine completed.")

    def _botSendmemeRoutine(self, context: CallbackContext) -> None:
        """Routine that send memes when it's time to do so."""
        logging.info("Sending memes routine begins.")

        if not self._queue:
            logging.error("Queue is empty. Aborting.")
            return

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        post = self._queue.pop(0)

        if post.video:
            logging.info(f"Sending video with path: {post.path}")
            context.bot.send_video(
                chat_id=channel_name, video=open(post.path, "rb"), caption=caption
            )
        else:
            logging.info(f"Sending image with path: {post.path}")
            context.bot.send_photo(
                chat_id=channel_name, photo=open(post.path, "rb"), caption=caption
            )

        self._downloader.deleteFile(post.path)

        logging.info("Sending memes routine completed.")

    async def _botError(self, update, context) -> None:
        """Send a message to admins whenever an error is raised."""
        message = "*ERROR RAISED*"
        # admin message
        for chat_id in self._settings["admins"]:
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )

        error_string = str(context.error)
        time_string = datetime.now().isoformat(sep=" ")

        message = (
            f"Error at time: {time_string}\n"
            f"Error raised: {error_string}\n"
            f"Update: {update}"
        )

        for chat_id in self._settings["admins"]:
            await context.bot.send_message(
                chat_id=chat_id, text=message, disable_web_page_preview=True
            )

        # logs to file
        logging.error(f"Update {update} caused error {context.error}.")

    # Bot commands
    async def _botStartCommand(self, update, context) -> None:
        """Start command handler."""
        chat_id = update.effective_chat.id

        message = (
            f"*Welcome in the Just Memes backend bot*\n"
            f"*This bot is used to manage the Just Memes meme channel*\n"
            f"_Join us at_ {self._settings['channel_name']}"
        )

        await context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
        )

    async def _botResetCommand(self, update, context) -> None:
        """Reset command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Resetting..._"

            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )

            logging.warning("Resetting...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        else:
            message = "*This command is for admins only*"
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )

    async def _botStopCommand(self, update, context) -> None:
        """Stop command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Bot stopped_"
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )
            self._updater.stop()
            logging.warning("Bot stopped.")
            os._exit()
        else:
            message = "*This command is for admins only*"
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
            )

    async def _botStatusCommand(self, update, context) -> None:
        """Status command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "\n\n".join(
                [
                    self._escapeMarkdown(str(x))
                    for x in [
                        self,
                        self._reddit,
                        self._database,
                        self._fingerprinter,
                        self._downloader,
                    ]
                ]
            )

        else:
            message = "*This command is for admins only*"

        await context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
        )

    async def _botNextpostCommand(self, update, context) -> None:
        """Nextpost command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            post_timestamp, preload_timestamp = self._getNextTimestamps()
            message = (
                "_The next meme is scheduled for:_ "
                f"{post_timestamp}\n"
                "_The next preload is scheduled for:_ "
                f"{preload_timestamp}"
            )

        else:
            message = "*This command is for admins only*"

        await context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
        )

    async def _botQueueCommand(self, update, context) -> None:
        """Queue command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            # check if any arg has been passed
            if len(context.args) == 0:
                if len(self._queue) > 0:
                    readable_queue = "\n".join([str(p) for p in self._queue])

                    message = f"_Current message queue:_\n" f"{readable_queue}"
                else:
                    message = (
                        "*The queue is empty*\n"
                        "_Pass the links as argument to set them_"
                    )
            else:
                image_count = 0
                for url in context.args:
                    # fingerprint it and add it to database
                    post = Post(
                        url=url,
                        video=self._downloader.isVideo(url),
                    )

                    post_path, preview_path = self._downloader.downloadMedia(post.url)

                    if not post_path:
                        continue

                    fingerprint = self._fingerprinter.fingerprint(
                        path=preview_path, url=post.url
                    )

                    # sometimes images cannot be fingerprinted
                    # if that happens, just skip it
                    if not fingerprint:
                        continue

                    # save the path of the file
                    post.setPath(post_path)
                    # update database
                    self._database.addData(post=post, fingerprint=fingerprint)
                    # add it to queue
                    self._queue.append(post)
                    # count as added
                    image_count += 1

                # wacky english
                plural = "s" if image_count > 1 else ""

                message = (
                    f"{image_count} _Image{plural} added to queue_\n"
                    "_Use /queue to check the current queue_"
                )
        else:
            message = "*This command is for admins only*"

        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

    async def _botCleanqueueCommand(self, update, context) -> None:
        """Cleanqueue command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            self._queue = []
            message = "Queue cleaned"
        else:
            message = "*This command is for admins only*"

        await context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=constants.ParseMode.MARKDOWN
        )

    async def _botPingCommand(self, update, context) -> None:
        """
        Ping command handler.

        This is just a quick way to check if the bot is still responding.
        All it does is reply PONG to a particular message.
        """
        chat_id = update.effective_chat.id
        await context.bot.send_chat_action(
            chat_id=chat_id, action=constants.ChatAction.TYPING
        )
        message = "🏓 *PONG* 🏓"
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=constants.ParseMode.MARKDOWN,
        )

    def start(self) -> None:
        """Start the bot and initialize all its components."""
        # create instances
        self._reddit = Reddit()
        self._database = Database()
        self._fingerprinter = Fingerprinter()
        self._downloader = MediaDownloader()

        # clear temp folder in downloader
        # sometimes, due to errors, files might be stuck in the temp folder
        self._downloader.cleanTempFolder()

        self._application = (
            Application.builder()
            .token(self._settings["token"])
            .defaults(Defaults(tzinfo=pytz.timezone(self._settings["timezone"])))
            .build()
        )
        self._jobqueue = self._application.job_queue

        # this routine will notify the admins
        self._jobqueue.run_once(self._botStartupRoutine, when=1, name="startup_routine")

        # this routine will clean the posted list
        self._jobqueue.run_daily(
            self._botCleanRoutine,
            time(0, 15, 0, 000000),
            name="new_day_routine",
        )

        # init Routines
        self._setMemesRoutineInterval()

        # these are the handlers for all the commands
        self._application.add_handler(CommandHandler("start", self._botStartCommand))
        self._application.add_handler(CommandHandler("reset", self._botResetCommand))
        self._application.add_handler(CommandHandler("stop", self._botStopCommand))
        self._application.add_handler(CommandHandler("status", self._botStatusCommand))
        self._application.add_handler(
            CommandHandler("nextpost", self._botNextpostCommand)
        )
        self._application.add_handler(CommandHandler("queue", self._botQueueCommand))
        self._application.add_handler(
            CommandHandler("cleanqueue", self._botCleanqueueCommand)
        )
        # hidden command, not in list
        self._application.add_handler(CommandHandler("ping", self._botPingCommand))

        # this handler will notify the admins and the user if something went
        #   wrong during the execution
        self._application.add_error_handler(self._botError)

        logging.info("Bot running.")
        self._application.run_polling()

    @property
    def words_to_skip(self) -> str:
        """Return the words to skip in the posts fingerprints."""
        return " ".join(sorted([s.lower() for s in self._settings["words_to_skip"]]))

    def __str__(self) -> str:
        """Return the bot's string representation."""
        post_timestamp, preload_timestamp = self._getNextTimestamps()
        ocr = "enabled" if self._settings["ocr"] else "off"
        preload_time = self._settings["preload_time"]
        start_delay = self._settings["start_delay"]
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"version: {self._version}",
                f"queue size: {len(self._queue)}",
                f"next post scheduled for: {post_timestamp}",
                f"next preload scheduled for: {preload_timestamp}",
                f"preload time: {preload_time} second{'s' if preload_time > 1 else ''}",
                f"start delay: {start_delay} second{'s' if preload_time > 1 else ''}",
                f"posts per day: {self._settings['posts_per_day']}",
                f"hash threshold: {self._settings['hash_threshold']}",
                f"max gif size: {self._settings['max_gif_size']} MB",
                f"ocr: {ocr}",
                f"words to skip: {self.words_to_skip}",
            ]
        )


def main():
    """Start the bot. Function automatically called whenever the script is run."""
    logging.basicConfig(
        filename=__file__.replace(".py", ".log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        filemode="w",
    )

    logging.info("Script started.")
    t = Telegram()
    logging.info("Telegram initialized.")
    t.start()


if __name__ == "__main__":
    main()
