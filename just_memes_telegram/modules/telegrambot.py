"""Telegram bot: schedules, filters and posts memes."""

import asyncio
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from imagehash import ImageHash
from loguru import logger
from telegram import Bot, LinkPreviewOptions, Update, constants
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    Job,
    JobQueue,
    Updater,
)

from just_memes_telegram import __version__
from just_memes_telegram.modules.common import (
    DEFAULT_SETTINGS_PATH,
    asyncRequest,
    loadSettings,
)
from just_memes_telegram.modules.data import Fingerprint, Post
from just_memes_telegram.modules.database import Database
from just_memes_telegram.modules.entities import DownloadResult
from just_memes_telegram.modules.fingerprinter import Fingerprinter
from just_memes_telegram.modules.mediadownloader import MediaDownloader, isGifUrl
from just_memes_telegram.modules.reddit import Reddit

SECONDS_PER_DAY = 24 * 60 * 60
BYTES_PER_MB = 1024 * 1024
RETRY_DELAY = 5
# raw error messages are truncated before escaping, so they fit in a message
MAX_ERROR_LENGTH = constants.MessageLimit.MAX_TEXT_LENGTH // 2
ADMIN_ONLY_MESSAGE = "*This command is for admins only*"

Context = ContextTypes.DEFAULT_TYPE


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

    _settings: dict[str, Any]
    _timezone: ZoneInfo
    _queue: list[Post]

    _reddit: Reddit
    _database: Database
    _fingerprinter: Fingerprinter
    _downloader: MediaDownloader
    _application: Application

    _send_memes_job: Job | None
    _preload_memes_job: Job | None
    _clean_database_job: Job | None

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        """Initialize the bot. Settings are automatically loaded.

        Args:
            settings_path (str | Path, optional): path of the settings file.
        """
        self._settings = loadSettings(settings_path, "Telegram")
        self._timezone = ZoneInfo(self._settings["timezone"])
        self._queue = []

        self._send_memes_job = None
        self._preload_memes_job = None
        self._clean_database_job = None

        self._started = False
        self._restart_requested = False
        self._stop_event = asyncio.Event()

        self._reddit = Reddit(settings_path)
        self._database = Database(settings_path)
        self._fingerprinter = Fingerprinter(settings_path)
        self._downloader = MediaDownloader(settings_path)

        self._application = (
            Application.builder()
            .token(self._settings["token"])
            .defaults(
                Defaults(
                    tzinfo=self._timezone,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
            )
            .build()
        )
        self._addHandlers()

    @property
    def _jobqueue(self) -> JobQueue:
        """The job queue of the application.

        Raises:
            RuntimeError: if the job queue is not available
        """
        if self._application.job_queue is None:
            msg = "The job queue is not available. Install PTB with [job-queue]."
            raise RuntimeError(msg)
        return self._application.job_queue

    @property
    def _updater(self) -> Updater:
        """The updater of the application.

        Raises:
            RuntimeError: if the updater is not available
        """
        if self._application.updater is None:
            msg = "The updater is not available."
            raise RuntimeError(msg)
        return self._application.updater

    @staticmethod
    def _escapeMarkdown(raw: str) -> str:
        """Replace markdown delimiters with escaped ones.

        Args:
            raw (str): un-escaped string

        Returns:
            str: markdown escaped string
        """
        for replace in ("_", "*", "[", "`"):
            raw = raw.replace(replace, f"\\{replace}")
        return raw

    @staticmethod
    def _getChatId(update: Update) -> int | None:
        """Return the id of the chat of an update.

        Args:
            update (Update): the update

        Returns:
            int | None: the chat id, if the update comes from a chat
        """
        return update.effective_chat.id if update.effective_chat else None

    def _now(self) -> datetime:
        """Return the current time in the bot timezone, without microseconds.

        Returns:
            datetime: the current time
        """
        return datetime.now(tz=self._timezone).replace(microsecond=0)

    def _getSecondsBetweenPosts(self) -> int:
        """Return number of seconds between consecutive posts.

        Returns:
            int: seconds between posts
        """
        return SECONDS_PER_DAY // self._settings["posts_per_day"]

    def _calculatePreload(self, now: datetime | None = None) -> tuple[float, str]:
        """Calculate seconds until next preload and its timestamp.

        Args:
            now (datetime, optional): current time. Defaults to now.

        Returns:
            tuple[float, str]: seconds until next preload and its timestamp
        """
        now = now or self._now()
        preload_time = timedelta(seconds=self._settings["preload_time"])
        seconds_between = timedelta(seconds=self._getSecondsBetweenPosts())
        start_delay = timedelta(seconds=self._settings["start_delay"])

        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_preload = midnight + start_delay - preload_time
        if next_preload <= now:
            # skip all the periods that are already in the past
            periods = (now - next_preload) // seconds_between + 1
            next_preload += periods * seconds_between

        return (next_preload - now).total_seconds(), next_preload.isoformat(sep=" ")

    def _calculateNextPost(self, now: datetime | None = None) -> tuple[float, str]:
        """Calculate seconds until next post and its timestamp.

        Args:
            now (datetime, optional): current time. Defaults to now.

        Returns:
            tuple[float, str]: seconds until next post and its timestamp
        """
        now = now or self._now()
        until_preload, _ = self._calculatePreload(now)
        next_post = now + timedelta(
            seconds=until_preload + self._settings["preload_time"]
        )
        return (next_post - now).total_seconds(), next_post.isoformat(sep=" ")

    @staticmethod
    def _jobTimestamp(job: Job | None) -> str:
        """Return the timestamp of the next run of a job.

        Args:
            job (Job | None): the job

        Returns:
            str: the timestamp, or "not scheduled"
        """
        if job is None or job.next_t is None:
            return "not scheduled"
        return job.next_t.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")

    def _getNextTimestamps(self) -> tuple[str, str]:
        """Return timestamps for next post and next preload.

        Returns:
            tuple[str, str]: next post and next preload timestamps
        """
        return (
            self._jobTimestamp(self._send_memes_job),
            self._jobTimestamp(self._preload_memes_job),
        )

    def _isAdmin(self, chat_id: int) -> bool:
        """Check if user is admin.

        Args:
            chat_id (int): chat id

        Returns:
            bool: True if the user is an admin
        """
        return chat_id in self._settings["admins"]

    @staticmethod
    async def _getFileSize(url: str) -> float | None:
        """Get file size (in MB) by the file's url.

        Args:
            url (str): file url

        Returns:
            float | None: size in MB, None if it cannot be determined
        """
        request = await asyncRequest(url, read_content=False)
        size = request.headers.get("content-length")
        if not request.is_successful or size is None or not size.isdigit():
            logger.error(
                "Cannot get file size. Status: {}. Url: {}.", request.status, url
            )
            return None

        return int(size) / BYTES_PER_MB

    async def _filterOldPosts(self, posts: list[Post]) -> list[Post]:
        """Remove already seen posts from the list.

        Args:
            posts (list[Post]): list of new posts

        Returns:
            list[Post]: posts that were never seen before
        """
        old_ids = await self._database.getOldIds()
        old_urls = await self._database.getOldUrls()
        return [p for p in posts if p.id not in old_ids and p.url not in old_urls]

    async def _checkGifSize(self, url: str) -> bool:
        """Check the size of a gif.

        Args:
            url (str): url of the gif

        Returns:
            bool: False if the gif is too big or its size is unknown
        """
        size = await self._getFileSize(url)
        if size is None:
            logger.warning("Skipping. Cannot get file size.")
            return False

        if size > self._settings["max_gif_size"]:
            logger.warning("Gif size is too big: {:.2f}MB", size)
            return False

        return True

    def _textValid(self, text: str | None) -> bool:
        """Check if a text does not contain blocked words (case insensitive).

        Args:
            text (str | None): text of the post

        Returns:
            bool: True if the text is valid
        """
        if not text:
            return True

        lower = text.lower()
        return not any(word in lower for word in self.words_to_skip)

    def _isRepost(self, img_hash: ImageHash, old_hashes: set[ImageHash]) -> bool:
        """Check if an image is too similar to an older one.

        Args:
            img_hash (ImageHash): hash of the new image
            old_hashes (set[ImageHash]): hashes of the old images

        Returns:
            bool: True if the image is a repost
        """
        threshold = self._settings["hash_threshold"]
        return any(
            Fingerprinter.compareHashes(img_hash, old) < threshold for old in old_hashes
        )

    def _setMemesRoutineInterval(self) -> None:
        """Create the routines that send memes, preload memes and clean the db."""
        until_preload, _ = self._calculatePreload()
        until_first, _ = self._calculateNextPost()
        seconds_between = self._getSecondsBetweenPosts()

        # remove the already scheduled jobs
        for job in (
            self._send_memes_job,
            self._preload_memes_job,
            self._clean_database_job,
        ):
            if job is not None:
                job.schedule_removal()

        self._send_memes_job = self._jobqueue.run_repeating(
            self._botSendmemeRoutine,
            interval=seconds_between,
            first=until_first,
            name="send_memes",
        )
        self._preload_memes_job = self._jobqueue.run_repeating(
            self._botPreloadmemeRoutine,
            interval=seconds_between,
            first=until_preload,
            name="preload_memes",
        )

        # the clean time is expressed in seconds after midnight
        clean_time = self._settings.get("clean_time", 0) % SECONDS_PER_DAY
        routine_time = time(
            hour=clean_time // 3600,
            minute=(clean_time // 60) % 60,
            second=clean_time % 60,
            tzinfo=self._timezone,
        )
        self._clean_database_job = self._jobqueue.run_daily(
            self._botCleanRoutine,
            time=routine_time,
            name="clean_database",
        )

    @staticmethod
    async def _safeSend(bot: Bot, chat_id: int | str, text: str) -> None:
        """Send a message, logging (instead of raising) any Telegram error.

        Args:
            bot (Bot): the bot used to send the message
            chat_id (int | str): recipient
            text (str): text of the message
        """
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except TelegramError:
            logger.exception("Cannot send message to {}.", chat_id)

    async def _notifyAdmins(self, bot: Bot, text: str) -> None:
        """Send a message to all the admins.

        Args:
            bot (Bot): the bot used to send the message
            text (str): text of the message
        """
        for chat_id in self._settings["admins"]:
            await self._safeSend(bot, chat_id, text)

    def _queuedPaths(self) -> list[str | None]:
        """Return the paths of the files of the queued posts.

        Returns:
            list[str | None]: paths of the queued posts
        """
        return [p.path for p in self._queue]

    async def _botStartupRoutine(self, context: Context) -> None:
        """Send a message to admins when the bot is started."""
        logger.info("Starting startup routine...")
        await self._notifyAdmins(context.bot, "*Bot started!*")
        next_post, next_preload = self._getNextTimestamps()
        logger.info("Next post: {}, next preload: {}", next_post, next_preload)

    async def _botCleanRoutine(self, _: Context) -> None:
        """Remove old posts from the database and old files from the temp folder."""
        logger.info("Clear database routine begins.")
        posts, fingerprints = await self._database.clean()
        logger.info(
            "Removed {} post and {} fingerprint documents from database.",
            posts,
            fingerprints,
        )
        # the files of the posts in queue are still needed
        self._downloader.cleanTempFolder(keep=self._queuedPaths())
        logger.info("Clear database routine completed.")

    async def _downloadAndFingerprint(
        self, post: Post
    ) -> tuple[DownloadResult, Fingerprint] | None:
        """Download the media of a post and fingerprint it.

        Args:
            post (Post): the post

        Returns:
            tuple[DownloadResult, Fingerprint] | None: the download result and the
                fingerprint, None if any of the steps failed
        """
        result = await self._downloader.downloadMedia(post.url)
        if not result.is_successful or result.path is None:
            logger.info("Skipping. Cannot download media: {}", post.url)
            return None

        fingerprint = await self._fingerprinter.fingerprint(
            img_url=post.url, img_path=result.preview_path, ocr=self._settings["ocr"]
        )
        # the preview is only needed to fingerprint videos
        if result.preview_path != result.path:
            self._downloader.deleteFile(result.preview_path)

        if fingerprint is None:
            logger.info("Skipping. Cannot fingerprint media: {}", post.url)
            self._downloader.deleteFile(result.path)
            return None

        return result, fingerprint

    async def _checkPost(self, post: Post, old_hashes: set[ImageHash]) -> bool:
        """Check if a post can be sent, downloading its media.

        Args:
            post (Post): the post to check
            old_hashes (set[ImageHash]): hashes of the old posts

        Returns:
            bool: True if the post is valid. Its path is then set.
        """
        logger.info("Checking post with url {}.", post.url)
        # this post can get approved or rejected
        # either way, it should not be scanned again
        await self._database.addData(post=post)

        if not self._textValid(post.title):
            logger.info("Skipping. Title contains skippable words: {}", post.title)
            return False

        if isGifUrl(post.url) and not await self._checkGifSize(post.url):
            return False

        downloaded = await self._downloadAndFingerprint(post)
        if downloaded is None:
            return False

        result, fingerprint = downloaded
        await self._database.addData(fingerprint=fingerprint)

        if fingerprint.hash is not None and self._isRepost(
            fingerprint.hash, old_hashes
        ):
            logger.warning("Skipping. Hash is too similar: {}", fingerprint.hash_str)
            self._downloader.deleteFile(result.path)
            return False

        if not self._textValid(fingerprint.caption):
            logger.warning("Skipping. Contains forbidden word: {}", fingerprint.caption)
            self._downloader.deleteFile(result.path)
            return False

        if result.path is not None:
            post.setPath(result.path)
        return True

    async def _botPreloadmemeRoutine(self, _: Context) -> None:
        """Preload a meme and put it into the queue."""
        logger.info("Preload memes routine begins.")

        if self._queue:
            logger.info(
                "Post already in queue: url: {}, path: {}.",
                self._queue[-1].url,
                self._queue[-1].path,
            )
            return

        posts = await self._reddit.fetch()
        to_check = await self._filterOldPosts(posts)
        logger.info("Filtered {} old posts.", len(posts) - len(to_check))

        # old hashes have to be loaded BEFORE the loop, because the fingerprint
        # of each checked post is added to the database as soon as it's created
        old_hashes = await self._database.getOldHashes()

        logger.info("Looking for a post between {} filtered posts.", len(to_check))
        for post in to_check:
            if await self._checkPost(post, old_hashes):
                self._queue.append(post)
                logger.info(
                    "Valid post added to queue. Url: {}, path: {}.",
                    post.url,
                    post.path,
                )
                return

        logger.error("No valid post found.")

    async def _botSendmemeRoutine(self, context: Context) -> None:
        """Send the first meme of the queue to the channel."""
        logger.info("Sending memes routine begins.")

        if not self._queue:
            logger.error("Queue is empty. Aborting.")
            return

        post = self._queue.pop(0)
        if post.path is None:
            logger.error("Post {} has no file. Aborting.", post.url)
            return

        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        media = Path(post.path)

        try:
            if post.video:
                logger.info("Sending video with path: {}", post.path)
                await context.bot.send_video(
                    chat_id=channel_name, video=media, caption=caption
                )
            else:
                logger.info("Sending image with path: {}", post.path)
                await context.bot.send_photo(
                    chat_id=channel_name, photo=media, caption=caption
                )
        finally:
            self._downloader.deleteFile(post.path)

        logger.info("Sending memes routine completed.")

    async def _botError(self, update: object, context: Context) -> None:
        """Log errors and send them to the admins."""
        error = context.error
        tb_string = (
            "".join(traceback.format_exception(error)) if error else "no traceback"
        )
        logger.error("Update {} caused error {}.\n{}", update, error, tb_string)

        messages = [
            f"Error at time: {self._now().isoformat(sep=' ')}\n",
            f"Error raised: {error}\n",
            f"Update: {update}",
            f"Traceback:\n{tb_string}",
        ]
        for message in messages:
            text = self._escapeMarkdown(message[-MAX_ERROR_LENGTH:])
            await self._notifyAdmins(context.bot, text)

    async def _requireAdmin(self, update: Update, context: Context) -> int | None:
        """Return the chat id if the user is an admin, otherwise reply with an error.

        Args:
            update (Update): the update
            context (Context): the context

        Returns:
            int | None: the chat id of the admin, None if the user is not admin
        """
        chat_id = self._getChatId(update)
        if chat_id is None:
            return None

        if not self._isAdmin(chat_id):
            await context.bot.send_message(chat_id=chat_id, text=ADMIN_ONLY_MESSAGE)
            return None

        return chat_id

    # Bot commands
    async def _botStartCommand(self, update: Update, context: Context) -> None:
        """Start command handler."""
        chat_id = self._getChatId(update)
        if chat_id is None:
            return

        message = (
            "*Welcome in the Just Memes backend bot*\n"
            "*This bot is used to manage the Just Memes meme channel*\n"
            f"_Join us at_ {self._settings['channel_name']}"
        )
        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botResetCommand(self, update: Update, context: Context) -> None:
        """Reset command handler."""
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        await context.bot.send_message(chat_id=chat_id, text="_Resetting..._")
        logger.warning("Resetting...")
        self.requestStop(restart=True)

    async def _botStopCommand(self, update: Update, context: Context) -> None:
        """Stop command handler."""
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        await context.bot.send_message(chat_id=chat_id, text="_Bot stopped_")
        logger.warning("Stopping...")
        self.requestStop()

    async def _botStatusCommand(self, update: Update, context: Context) -> None:
        """Status command handler."""
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        descriptions = [
            str(self),
            str(self._reddit),
            await self._database.describe(),
            str(self._fingerprinter),
            str(self._downloader),
        ]
        message = "\n\n".join(self._escapeMarkdown(d) for d in descriptions)
        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botNextpostCommand(self, update: Update, context: Context) -> None:
        """Nextpost command handler."""
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        post_timestamp, preload_timestamp = self._getNextTimestamps()
        message = (
            f"_The next meme is scheduled for:_ {post_timestamp}\n"
            f"_The next preload is scheduled for:_ {preload_timestamp}"
        )
        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _showQueue(self, bot: Bot, chat_id: int) -> None:
        """Show queue to user."""
        if self._queue:
            readable_queue = "\n".join(str(p) for p in self._queue)
            message = (
                f"_Current message queue:_\n{self._escapeMarkdown(readable_queue)}"
            )
        else:
            message = "*The queue is empty*\n_Pass the links as argument to set them_"

        await bot.send_message(chat_id=chat_id, text=message)

    async def _addUrlsToQueue(self, bot: Bot, chat_id: int, urls: list[str]) -> int:
        """Download, fingerprint and add urls to the queue.

        Args:
            bot (Bot): the bot used to report errors
            chat_id (int): chat to report errors to
            urls (list[str]): urls to add

        Returns:
            int: number of urls added to the queue
        """
        count = 0
        for url in urls:
            logger.info("Adding url to queue: {}", url)
            post = Post(url=url, video=self._downloader.isVideo(url))

            downloaded = await self._downloadAndFingerprint(post)
            if downloaded is None:
                await bot.send_message(
                    chat_id=chat_id,
                    text=self._escapeMarkdown(f"Cannot add media: {url}"),
                )
                continue

            result, fingerprint = downloaded
            if result.path is not None:
                post.setPath(result.path)
            await self._database.addData(post=post, fingerprint=fingerprint)
            self._queue.append(post)
            count += 1
            logger.info("Added url to queue: {}", url)

        return count

    async def _botQueueCommand(self, update: Update, context: Context) -> None:
        """Queue command handler."""
        logger.info("Queue command received.")
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        # if no arg has been passed, just show the queue
        if not context.args:
            await self._showQueue(context.bot, chat_id)
            return

        count = await self._addUrlsToQueue(context.bot, chat_id, context.args)
        plural = "" if count == 1 else "s"
        message = (
            f"{count} _Image{plural} added to queue_\n"
            "_Use /queue to check the current queue_"
        )
        await context.bot.send_message(chat_id=chat_id, text=message)

    async def _botCleanqueueCommand(self, update: Update, context: Context) -> None:
        """Cleanqueue command handler."""
        chat_id = await self._requireAdmin(update, context)
        if chat_id is None:
            return

        for path in self._queuedPaths():
            self._downloader.deleteFile(path)
        self._queue = []
        await context.bot.send_message(chat_id=chat_id, text="Queue cleaned")

    async def _botPingCommand(self, update: Update, context: Context) -> None:
        """Ping command handler.

        This is just a quick way to check if the bot is still responding.
        """
        chat_id = self._getChatId(update)
        if chat_id is None:
            return

        await context.bot.send_chat_action(
            chat_id=chat_id, action=constants.ChatAction.TYPING
        )
        await context.bot.send_message(chat_id=chat_id, text="🏓 *PONG* 🏓")

    def _addHandlers(self) -> None:
        """Register the command and error handlers."""
        commands = {
            "start": self._botStartCommand,
            "reset": self._botResetCommand,
            "stop": self._botStopCommand,
            "status": self._botStatusCommand,
            "nextpost": self._botNextpostCommand,
            "queue": self._botQueueCommand,
            "cleanqueue": self._botCleanqueueCommand,
            # hidden command, not in list
            "ping": self._botPingCommand,
        }
        for command, callback in commands.items():
            self._application.add_handler(CommandHandler(command, callback))

        self._application.add_error_handler(self._botError)

    async def _initializeApplication(self, *, wait_connection: bool) -> None:
        """Initialize the application, retrying on network errors if requested.

        Args:
            wait_connection (bool): whether to retry until the network is up

        Raises:
            NetworkError: if the connection fails and wait_connection is False
        """
        while True:
            try:
                await self._application.initialize()
            except NetworkError:
                if not wait_connection:
                    raise
                logger.warning("Network error. Retrying in {} seconds...", RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)
            else:
                return

    async def start(self, *, wait_connection: bool = False) -> None:
        """Start the bot.

        Args:
            wait_connection (bool, optional): If True, the bot will wait for
                the connection to be established before starting. Defaults to False.
        """
        logger.info("Starting bot...")
        await self._database.connect()
        # sometimes, due to errors, files might be stuck in the temp folder
        self._downloader.cleanTempFolder()

        await self._initializeApplication(wait_connection=wait_connection)

        self._setMemesRoutineInterval()
        # this routine will notify the admins
        self._jobqueue.run_once(self._botStartupRoutine, when=1, name="startup")

        await self._application.start()
        await self._updater.start_polling()
        self._started = True
        logger.info("Bot running.")

    async def stop(self) -> None:
        """Stop the bot, notifying the admins and releasing all the resources."""
        logger.info("Stopping bot...")

        if self._started:
            await self._notifyAdmins(self._application.bot, "*Bot stopped!*")
            await self._updater.stop()
            await self._application.stop()
            await self._application.shutdown()
            self._started = False

        self._downloader.cleanTempFolder()
        await self._reddit.close()
        await self._database.close()
        logger.info("Bot stopped.")

    def requestStop(self, *, restart: bool = False) -> None:
        """Ask the bot to stop. The caller of `waitForStopRequest` handles it.

        Args:
            restart (bool, optional): whether the bot should be restarted.
        """
        self._restart_requested = self._restart_requested or restart
        self._stop_event.set()

    async def waitForStopRequest(self) -> None:
        """Wait until a stop is requested (via command or signal)."""
        await self._stop_event.wait()

    @property
    def restart_requested(self) -> bool:
        """Whether a restart was requested."""
        return self._restart_requested

    @property
    def words_to_skip(self) -> list[str]:
        """The (lowercase) words that make a post invalid."""
        return sorted(s.lower() for s in self._settings["words_to_skip"] if s)

    @property
    def words_to_skip_str(self) -> str:
        """The words that make a post invalid, as a string."""
        return ", ".join(self.words_to_skip)

    def __repr__(self) -> str:
        """Return the bot's string representation."""

        def plural(n: int) -> str:
            return "" if n == 1 else "s"

        post_timestamp, preload_timestamp = self._getNextTimestamps()
        ocr = "enabled" if self._settings["ocr"] else "off"
        preload_time = self._settings["preload_time"]
        start_delay = self._settings["start_delay"]
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"version: {__version__}",
                f"queue size: {len(self._queue)}",
                f"next post scheduled for: {post_timestamp}",
                f"next preload scheduled for: {preload_timestamp}",
                f"preload time: {preload_time} second{plural(preload_time)}",
                f"start delay: {start_delay} second{plural(start_delay)}",
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
