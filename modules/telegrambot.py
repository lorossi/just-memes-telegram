"""Main bot script."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, time, timedelta

import pytz
import requests
import ujson
from telegram import constants, Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    Defaults,
    Job,
    Updater,
)

from modules.data import Post
from modules.database import Database
from modules.fingerprinter import Fingerprinter
from modules.mediadownloader import MediaDownloader
from modules.reddit import Reddit

from typing import Any


class TelegramBot:
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

    _version: str = "2.2.0"
    _settings_path: str = "settings/settings.json"
    _running_async: bool = False

    _settings: dict[str, Any]
    _queue: list[Post]

    _reddit: Reddit
    _application: Application
    _updater: Updater
    _send_memes_job: Job
    _preload_memes_job: Job
    _clean_database_job: Job

    def __init__(self) -> TelegramBot:
        """Initialize the bot. Settings are automatically loaded."""
        self._queue = []
        self._send_memes_job = None
        self._preload_memes_job = None
        self._clean_database_job = None

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
            until_preload (int, optional): Seconds until next preload.
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

    def _filterOldPosts(self, posts: list[Post]) -> list[Post]:
        """Remove old posts from the list.

        Args:
            posts (list[Post]): list of new posts

        Returns:
            list[Post]
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
        """Check if the post text is valid
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
        """Create routines to:
        - send memes
        - preload memes
        - clean database
        """

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

        # then take care of clean database job
        # remove the old job, if already set
        if self._clean_database_job:
            self._clean_database_job.schedule_removal()

        # set new routine
        # the time is set to midnight + a delay specified in the settings
        seconds = self._settings["clean_time"] % 60
        minutes = (self._settings["clean_time"] // 60) % 60
        hours = self._settings["clean_time"] // 3600

        routine_time = time(
            hour=hours,
            minute=minutes,
            second=seconds,
        )
        self._clean_database_job = self._jobqueue.run_daily(
            self._botCleanRoutine,
            time=routine_time,
            name="clean_database",
        )

    async def _botStartupRoutine(self, _: CallbackContext) -> None:
        """Send a message to admins when the bot is started."""
        logging.info("Starting startup routine...")

        message = "*Bot started!*"
        for chat_id in self._settings["admins"]:
            await self._application.bot.send_message(chat_id=chat_id, text=message)

        next_post, next_preload = self._getNextTimestamps()
        logging.info(f"Next post: {next_post}, next preload: {next_preload}")

        logging.info("Startup routine completed.")

    async def _botCleanRoutine(self, _: CallbackContext) -> None:
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

    async def _postStopRoutine(self) -> None:
        """Send a message to admins when the bot is stopped."""
        logging.info("Starting post stop routine...")

        message = "*Bot stopped!*"
        for chat_id in self._settings["admins"]:
            await self._application.bot.send_message(chat_id=chat_id, text=message)

        # clean the temp folder
        self._downloader.cleanTempFolder()

        logging.info("Post stop routine completed.")

    async def _botPreloadmemeRoutine(self, _: CallbackContext) -> None:
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
        posts = await self._reddit.fetch()

        # filter old posts
        len_before = len(posts)
        to_check = self._filterOldPosts(posts)
        len_after = len(to_check)
        logging.info(f"Filtered {len_before - len_after} old posts.")

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
                logging.info(f"Skipping. Cannot download media: {post.url}")
                continue

            # fingerprint the post
            fingerprint = self._fingerprinter.fingerprint(
                img_path=preview_path, img_url=post.url
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

        if not self._queue:
            logging.error("No valid post found. Aborting.")
            return

        logging.info(
            f"Valid post added to queue. Url: {self._queue[-1].url}, "
            f"path: {self._queue[-1].path}."
        )

        logging.info("Preload memes routine completed.")

    async def _botSendmemeRoutine(self, _: CallbackContext) -> None:
        """Routine that send memes when it's time to do so."""
        logging.info("Sending memes routine begins.")

        if not self._queue:
            logging.error("Queue is empty. Aborting.")
            return

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        post = self._queue.pop(0)
        # load the media
        media = open(post.path, "rb")

        if post.video:
            logging.info(f"Sending video with path: {post.path}")
            await self._application.bot.send_video(
                chat_id=channel_name, video=media, caption=caption
            )
        else:
            logging.info(f"Sending image with path: {post.path}")
            await self._application.bot.send_photo(
                chat_id=channel_name, photo=media, caption=caption
            )

        self._downloader.deleteFile(post.path)

        logging.info("Sending memes routine completed.")

    async def _botError(self, update: Update, context: CallbackContext) -> None:
        """Send a message to admins whenever an error is raised."""
        message = "*ERROR RAISED*"
        # admin message
        for chat_id in self._settings["admins"]:
            await self._application.bot.send_message(chat_id=chat_id, text=message)

        error_string = str(context.error)
        time_string = datetime.now().isoformat(sep=" ")

        message = (
            f"Error at time: {time_string}\n"
            f"Error raised: {error_string}\n"
            f"Update: {update}"
        )

        for chat_id in self._settings["admins"]:
            await self._application.bot.send_message(chat_id=chat_id, text=message)

        # logs to file
        logging.error(f"Update {update} caused error {context.error}.")

    # Bot commands
    async def _botStartCommand(self, update: Update, context: CallbackContext) -> None:
        """Start command handler."""
        chat_id = update.effective_chat.id

        message = (
            f"*Welcome in the Just Memes backend bot*\n"
            f"*This bot is used to manage the Just Memes meme channel*\n"
            f"_Join us at_ {self._settings['channel_name']}"
        )

        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botResetCommand(self, update: Update, context: CallbackContext) -> None:
        """Reset command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Resetting..._"

            await context.bot.send_message(chat_id=chat_id, text=message)

            logging.warning("Resetting...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        else:
            message = "*This command is for admins only*"
            await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botStopCommand(self, update: Update, context: CallbackContext) -> None:
        """Stop command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Bot stopped_"
            await context.bot.send_message(chat_id=chat_id, text=message)
            self._updater.stop()
            logging.warning("Bot stopped.")
            os._exit()
        else:
            message = "*This command is for admins only*"
            await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botStatusCommand(self, update: Update, context: CallbackContext) -> None:
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

        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botNextpostCommand(
        self, update: Update, context: CallbackContext
    ) -> None:
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

        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botQueueCommand(self, update: Update, context: CallbackContext) -> None:
        """Queue command handler."""
        logging.info("Queue command received.")
        chat_id = update.effective_chat.id

        if not self._isAdmin(chat_id):
            message = "*This command is for admins only*"
            await context.bot.send_message(chat_id=chat_id, text=message)
            return

        # check if any arg has been passed
        if len(context.args) == 0:
            # no args, pass the queue
            if len(self._queue) > 0:
                # at least one post in queue
                readable_queue = "\n".join([str(p) for p in self._queue])
                message = f"_Current message queue:_\n" f"{readable_queue}"
            else:
                # queue is empty
                message = (
                    "*The queue is empty*\n" "_Pass the links as argument to set them_"
                )

            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
            )
            return

        image_count = 0
        for url in context.args:
            logging.info(f"Adding url to queue: {url}")
            # fingerprint it and add it to database
            post = Post(
                url=url,
                video=self._downloader.isVideo(url),
            )

            # download the media
            post_path, preview_path = self._downloader.downloadMedia(post.url)
            # no path = the download failed, raise error
            if not post_path:
                logging.error(f"Cannot download media: {post.url}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Cannot download media: {post.url}",
                )
                continue

            fingerprint = self._fingerprinter.fingerprint(
                img_path=preview_path, img_url=post.url
            )

            # sometimes images cannot be fingerprinted
            # if that happens, just skip it
            if not fingerprint:
                logging.warning(f"Cannot fingerprint media: {post.url}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Cannot fingerprint media: {post.url}",
                )
                continue

            # save the path of the file
            post.setPath(post_path)
            # update database
            self._database.addData(post=post, fingerprint=fingerprint)
            # add it to queue
            self._queue.append(post)
            # count as added
            image_count += 1
            logging.info(f"Added url to queue: {url}")

        # wacky english
        plural = "" if image_count == 1 else "s"
        message = (
            f"{image_count} _Image{plural} added to queue_\n"
            "_Use /queue to check the current queue_"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
        )

    async def _botCleanqueueCommand(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Cleanqueue command handler."""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            self._queue = []
            message = "Queue cleaned"
        else:
            message = "*This command is for admins only*"

        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botPingCommand(self, update: Update, context: CallbackContext) -> None:
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
        )

    def _setupApplication(self) -> None:
        """Initialise all the components of the bot."""
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
            .defaults(
                Defaults(
                    tzinfo=pytz.timezone(self._settings["timezone"]),
                    disable_web_page_preview=True,
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
            )
            .pool_timeout(10)
            .get_updates_http_version("1.1")
            .http_version("1.1")
            .build()
        )
        self._jobqueue = self._application.job_queue
        self._updater = self._application.updater

        # this routine will notify the admins
        self._jobqueue.run_once(self._botStartupRoutine, when=1, name="startup_routine")

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

    def start(self) -> None:
        logging.info("Bot running.")
        self._setupApplication()
        self._application.run_polling()
        self._running_async = False

    async def startAsync(self) -> None:
        logging.info("Starting bot in async mode...")
        self._setupApplication()
        await self._application.initialize()
        await self._updater.start_polling()
        await self._application.start()
        self._application.post_stop = self._postStopRoutine
        self._running_async = True
        logging.info("Bot running in async mode.")

    async def stopAsync(self) -> None:
        """Stop the bot."""
        logging.info("Stopping bot...")
        if not self._running_async:
            raise RuntimeError("Bot is not running.")

        await self._updater.stop()
        await self._application.stop()
        await self._application.shutdown()

        logging.info("Bot stopped.")

    @property
    def words_to_skip(self) -> list[str]:
        """Return the words to skip in the posts fingerprints."""
        return sorted(s.lower() for s in self._settings["words_to_skip"])

    @property
    def words_to_skip_str(self) -> str:
        """Return the words to skip in the posts fingerprints."""
        return ", ".join(self.words_to_skip)

    def __repr__(self) -> str:
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
                f"words to skip: {self.words_to_skip_str}",
            ]
        )

    def __str__(self) -> str:
        """Return the bot's string representation."""
        return self.__repr__()
