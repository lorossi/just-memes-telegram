import os
import sys
import pytz
import ujson
import logging

from time import time
from datetime import datetime, time, timedelta
from telegram import ParseMode, ChatAction
from telegram.ext import Updater, CommandHandler, CallbackContext, Defaults

from data import Post
from reddit import Reddit
from database import Database
from fingerprinter import Fingerprinter
from videodownloader import VideoDownloader


class Telegram:
    """Class that handles all the Telegram stuff
    BOTFATHER commands description
    start - view start message
    reset - reloads the bot
    stop - stops the bot
    status - show some infos about the bot
    nextpost - show at what time the next post is
    queue - view queue and add image(s) url(s) to queue
    subreddits - views and sets source subreddits
    postsperday - views and sets number of posts per day
    cleanqueue - cleans queue
    """

    def __init__(self):
        self._version = "2.0.0"  # current bot version
        self._settings_path = "settings/settings.json"
        self._settings = []
        self._queue = []
        self._send_memes_job = None
        self._preload_memes_job = None

        self._loadSettings()

    # Private methods

    def _loadSettings(self) -> None:
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Telegram"]

    def _saveSettings(self) -> None:
        """Saves settings to file"""
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Telegram"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _escapeMarkdown(self, str) -> str:
        """Replaces markdown delimites with escaped ones"""
        to_replace = ["_", "*", "[", "`"]
        for replace in to_replace:
            str = str.replace(replace, f"\\{replace}")
        return str

    def _secondsBetweenPosts(self) -> int:
        return int(24 * 60 * 60 / self._settings["posts_per_day"])

    def _calculateNextPost(self, until_preload: int = None) -> tuple[int, str]:
        """Calculates seconds until next post and its timestamp

        Args:
            until_preload (int, optional): Seconds until next preload. If None, it's recalculated.

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
        """Calculates seconds until next preload and its timestamp

        Returns:
            tuple[int, str]: seconds until next preload and its timestamp
        """
        # convert preload time into timedelta
        preload_time = timedelta(seconds=self._settings["preload_time"])
        # convert firest post into timedelta
        # convert second between posts into timedelta
        seconds_between = timedelta(seconds=self._secondsBetweenPosts())
        # convert start delay into timedelta
        delay_minutes = timedelta(seconds=self._settings["start_delay"])
        # remove seconds and microseconds from now
        now = datetime.now().replace(microsecond=0)
        # starting time
        next_preload = (
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            + delay_minutes
            - preload_time
        )
        # loop until it's in the future
        while next_preload <= now:
            next_preload += seconds_between

        return (next_preload - now).seconds, next_preload.isoformat(sep=" ")

    def _nextTimestamps(self) -> tuple[str, str]:
        """Returns timestamps for next post and next preload

        Returns:
            tuple[str, str]
        """

        return (
            x.next_t.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
            for x in [
                self._send_memes_job,
                self._preload_memes_job,
            ]
        )

    def _isAdmin(self, chat_id: str) -> bool:
        return chat_id in self._settings["admins"]

    def _setMemesRoutineInterval(self) -> None:
        """Create routine to send memes."""
        until_preload, _ = self._calculatePreload()
        until_first, _ = self._calculateNextPost(until_preload)
        seconds_between = self._secondsBetweenPosts()
        # we remove already started jobs from the schedule
        # (this happens when we change the number of posts per day or
        # the preload time)

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

    # Bot routines

    def _botStartupRoutine(self, context: CallbackContext) -> None:
        """Sends a message to admins when the bot is started"""
        logging.info("Starting startup routine...")

        message = "*Bot started!*"
        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

        logging.info("Startup routine completed.")

    def _botClearDatabaseRoutine(self, context: CallbackContext) -> None:
        """Routines that handles the removal of old posts"""
        logging.info("Clear database routine begins.")

        posts, fingerprints = self._database.clean()
        logging.info(
            f"Removed {posts} post and {fingerprints} fingerprint documents "
            "of old data removed from database."
        )

        logging.info("Clear database routine completed.")

    def _botPreloadmemeRoutine(self, _: CallbackContext) -> None:
        """Routine that preloads memes and puts them into queue"""
        logging.info("Preload memes routine begins.")
        # load url from reddit

        if not self._queue:
            # no urls on queue, fetching a new one

            # load old ids, hashes and urls
            old_ids = self._database.getOldIds()
            old_hashes = self._database.getOldHashes()
            old_urls = self._database.getOldUrls()
            # filter images that match old ids or old urls
            to_check = [
                p
                for p in self._reddit.fetch(include_videos=True)
                if p.id not in old_ids and p.url not in old_urls
            ]

            logging.info(f"Looking for a post between {len(to_check)} preloaded posts.")

            for post in to_check:
                # this post can get approved or rejected
                # either way, it should not be scanned again
                self._database.addData(post=post)

                # check if title contains anything not permitted
                if any(s in post.title for s in self._settings["words_to_skip"]):
                    logging.info("Skipping. Title contains skippable words.")
                    # update database
                    continue

                # if video is url, skip fingerprinting
                if post.video:
                    path = self._video_downloader.downloadVideo(post.url)
                    if path:
                        post.path = path
                    else:
                        continue
                else:
                    # fingerprint the post
                    fingerprint = self._fingerprinter.fingerprint(post.url)
                    # update the database with post and fingerprint
                    self._database.addData(fingerprint=fingerprint)

                    # check if the new post is too similar to an older one
                    if any(
                        abs(fingerprint.hash - f) < self._settings["hash_threshold"]
                        for f in old_hashes
                    ):
                        logging.info("Skipping. Too similar to past image.")
                        continue

                    # check if caption contains anything not permitted
                    if self._settings["ocr"] and fingerprint.caption:
                        if any(
                            s in fingerprint.caption
                            for s in self._settings["words_to_skip"]
                        ):
                            logging.info("Skipping. Caption contains skippable words.")
                            continue

                # a post has been found
                # adds the photo to bot queue so we can use this later
                self._queue.append(post)
                break

            logging.info(f"Posts found. Post url: {self._queue[-1].url}.")
        else:
            logging.info(f"Post already in queue.. Post url: {self._queue[-1].url}.")

        logging.info("Preload memes routine completed.")

    def _botSendmemeRoutine(self, context: CallbackContext) -> None:
        """Routine that send memes when it's time to do so"""
        logging.info("Sending memes routine begins.")

        if not self._queue:
            logging.error("Queue is empty. Aborting.")
            return

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        post = self._queue.pop(0)
        new_url = post.url
        logging.info(f"Sending image with url {new_url}.")

        if not post.video:
            context.bot.send_photo(chat_id=channel_name, photo=new_url, caption=caption)
        else:
            context.bot.send_video(
                chat_id=channel_name, video=open(post.path, "rb"), caption=caption
            )
            self._video_downloader.deleteVideo()

        logging.info("Sending memes routine completed.")

    def _botError(self, update, context) -> None:
        """Function that sends a message to admins whenever
        an error is raised"""
        message = "*ERROR RAISED*"
        # admin message
        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

        error_string = str(context.error)
        time_string = datetime.now().isoformat(sep=" ")

        message = (
            f"Error at time: {time_string}\n"
            f"Error raised: {error_string}\n"
            f"Update: {update}"
        )

        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id, text=message, disable_web_page_preview=True
            )

        # logs to file
        logging.error(f"Update {update} caused error {context.error}.")

    # Bot commands
    def _botStartCommand(self, update, context) -> None:
        """Function handling start command"""
        chat_id = update.effective_chat.id

        message = (
            f"*Welcome in the Just Memes backend bot*\n"
            f"*This bot is used to manage the Just Memes meme channel*\n"
            f"_Join us at_ {self._settings['channel_name']}"
        )

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botResetCommand(self, update, context) -> None:
        """Function handling reset command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Resetting..._"

            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

            logging.warning("Resetting...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        else:
            message = "*This command is for admins only*"
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

    def _botStopCommand(self, update, context) -> None:
        """Function handling stop command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            message = "_Bot stopped_"
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )
            self._updater.stop()
            logging.warning("Bot stopped.")
            os._exit()
        else:
            message = "*This command is for admins only*"
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

    def _botStatusCommand(self, update, context) -> None:
        """Function handling status command"""
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
                        self._video_downloader,
                    ]
                ]
            )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botNextpostCommand(self, update, context) -> None:
        """Function handling nextpost command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            post_timestamp, preload_timestamp = self._nextTimestamps()
            message = (
                "_The next meme is scheduled for:_ "
                f"{post_timestamp}.\n"
                "_The next preload is scheduled for:_ "
                f"{preload_timestamp}."
            )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botQueueCommand(self, update, context) -> None:
        """Function handling queue command"""
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
                for url in context.args:
                    # an url been passed
                    # fingerprint it and add it to database
                    post = Post(url=url, timestamp=time())
                    fingerprint = self._fingerprinter.fingerprint(post)
                    self._database.addData(post=post, fingerprint=fingerprint)
                    # add it to queue
                    self._queue.append(post)

                message = (
                    "_Image(s) added to queue_\n"
                    "_Use /queue to check the current queue_"
                )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

    def _botSubredditsCommand(self, update, context) -> None:
        """Function handling subreddits command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            if len(context.args) == 0:
                subreddits = self._reddit.subreddits
                subreddits_list = "\n".join(subreddits).replace("_", "\\_")
                message = (
                    "_Current subreddits:_\n"
                    f"{subreddits_list}"
                    "\n_Pass the subreddit list as argument to set them_"
                )

            else:
                self._reddit.subreddits = context.args
                subreddits_list = "\n".join(context.args).replace("_", "\\_")

                message = "_New subreddit list:_\n" f"{subreddits_list}"

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botPostsperdayCommand(self, update, context) -> None:
        """Function handling postsperday command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            if len(context.args) == 0:
                message = (
                    f"_Posts per day:_ {self._settings['posts_per_day']}\n"
                    "_Pass the number of posts per day as argument "
                    "to set a new value_"
                )
            else:
                try:
                    self._settings["posts_per_day"] = int(context.args[0])
                    self._saveSettings()
                    self._setMemesRoutineInterval()
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
                    )
                    return
            # notify user
            message = f"_Number of posts per day:_ {self._settings['posts_per_day']}"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botCleanqueueCommand(self, update, context) -> None:
        """Function handling cleanqueue command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            self._queue = []
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botPingCommand(self, update, context) -> None:
        """Function handling ping command"""
        chat_id = update.effective_chat.id
        context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = "🏓 *PONG* 🏓"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def start(self) -> None:
        """Function that starts the bot in all its components"""

        # create instances
        self._reddit = Reddit()
        self._database = Database()
        self._fingerprinter = Fingerprinter()
        self._video_downloader = VideoDownloader()

        # set Defaults
        defaults = Defaults(tzinfo=pytz.timezone(self._settings["timezone"]))
        # start the bot
        self._updater = Updater(
            self._settings["token"], use_context=True, defaults=defaults
        )
        self._dispatcher = self._updater.dispatcher
        self._jobqueue = self._updater.job_queue

        # this routine will notify the admins
        self._jobqueue.run_once(self._botStartupRoutine, when=1, name="startup_routine")

        # this routine will clean the posted list
        self._jobqueue.run_daily(
            self._botClearDatabaseRoutine,
            time(0, 15, 0, 000000),
            name="new_day_routine",
        )

        # init Routines
        self._setMemesRoutineInterval()

        # these are the handlers for all the commands
        self._dispatcher.add_handler(CommandHandler("start", self._botStartCommand))

        self._dispatcher.add_handler(CommandHandler("reset", self._botResetCommand))

        self._dispatcher.add_handler(CommandHandler("stop", self._botStopCommand))

        self._dispatcher.add_handler(CommandHandler("status", self._botStatusCommand))

        self._dispatcher.add_handler(
            CommandHandler("nextpost", self._botNextpostCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler("queue", self._botQueueCommand, pass_args=True)
        )

        self._dispatcher.add_handler(
            CommandHandler("subreddits", self._botSubredditsCommand, pass_args=True)
        )

        self._dispatcher.add_handler(
            CommandHandler("postsperday", self._botPostsperdayCommand, pass_args=True)
        )

        self._dispatcher.add_handler(
            CommandHandler("cleanqueue", self._botCleanqueueCommand)
        )

        # hidden command, not in list
        self._dispatcher.add_handler(CommandHandler("ping", self._botPingCommand))

        # this handler will notify the admins and the user if something went
        #   wrong during the execution
        self._dispatcher.add_error_handler(self._botError)

        self._updater.start_polling()
        logging.info("Bot running.")
        self._updater.idle()

    def __str__(self) -> str:
        post_timestamp, preload_timestamp = self._nextTimestamps()
        ocr = "enabled" if self._settings["ocr"] else "off"
        return "\n\t· ".join(
            [
                f"Telegram Bot:",
                f"version: {self._version}",
                f"queue size: {len(self._queue)}",
                f"next post scheduled for: {post_timestamp}",
                f"next preload scheduled for: {preload_timestamp}",
                f"preload time: {self._settings['preload_time']} seconds(s)",
                f"start delay: {self._settings['start_delay']} second(s)",
                f"posts per day: {self._settings['posts_per_day']}",
                f"ocr: {ocr}",
                f"hash threshold: {self._settings['hash_threshold']}",
            ]
        )


def main():
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
