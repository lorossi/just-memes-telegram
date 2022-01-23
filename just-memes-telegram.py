import os
import sys
import ujson
import logging
from time import sleep, time
from datetime import datetime, time, timedelta
from telegram import ParseMode, ChatAction
from telegram.ext import Updater, CommandHandler, CallbackContext

from reddit import Reddit
from database import Database
from fingerprinter import Fingerprinter
from data import Post


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
    version - show bot version
    """

    def __init__(self):
        self._version = "2.0.0"  # current bot version
        self._settings_path = "settings/settings.json"
        self._settings = []
        self._queue = []
        self._send_memes_job = None
        self._preload_memes_job = None
        self._next_post_timestamp = None

        self._loadSettings()

    # Private methods

    def _loadSettings(self):
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Telegram"]

    def _saveSettings(self):
        """Saves settings to file"""
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Telegram"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _escapeMarkdown(self, str):
        """Replaces markdown delimites with escaped ones"""
        to_replace = ["_", "*", "[", "`"]
        for replace in to_replace:
            str = str.replace(replace, f"\\{replace}")
        return str

    def _calculateTiming(self):
        """Calculates seconds between posts and until next post"""

        # calculation of time between messages
        self._minutes_between_messages = int(24 * 60 / self._settings["posts_per_day"])
        # convert minutes between messages into timedelta
        minutes_between_messages = timedelta(minutes=self._minutes_between_messages)
        # convert start delay into timedelta
        delay_minutes = timedelta(minutes=self._settings["start_delay"])
        # convert preload time into timedelta
        preload_time = timedelta(minutes=self._settings["preload_time"])

        # starting time
        midnight = (
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            + delay_minutes
        )
        # remove seconds and microseconds from now
        now = datetime.now().replace(second=0, microsecond=0)

        # Initialize the loop to add an interval until we are in the future
        next_post = midnight
        while next_post - preload_time <= now:
            next_post += minutes_between_messages
        # Do the same but without accounting for preload
        next_post_no_preload = midnight
        while next_post_no_preload <= now:
            next_post_no_preload += minutes_between_messages

        # To avoid rounding errors we recalculate the current time
        now = datetime.now()
        # we add 30 seconds to actual time in order to make it closer to
        # the real deal
        seconds_until_next_post = (next_post - now).seconds + 30
        seconds_until_next_preload = (next_post - now - preload_time).seconds

        return {
            "seconds_between_posts": 60 * self._minutes_between_messages,
            "seconds_until_first_post": seconds_until_next_post,
            "seconds_until_first_preload": seconds_until_next_preload,
            "next_post_timestamp": next_post.isoformat(),
            "next_post_timestamp_no_preload": next_post_no_preload.isoformat(),
        }

    def _isAdmin(self, chat_id: str) -> bool:
        return chat_id in self._settings["admins"]

    def _setMemesRoutineInterval(self):
        """Create routine to send memes."""
        timing = self._calculateTiming()
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
            interval=timing["seconds_between_posts"],
            first=timing["seconds_until_first_post"],
            name="send_memes",
        )

        # then take care of preload memes job
        # remove the old job, if already set
        if self._preload_memes_job:
            self._preload_memes_job.schedule_removal()

        # set new routine
        self._preload_memes_job = self._jobqueue.run_repeating(
            self._botPreloadmemeRoutine,
            interval=timing["seconds_between_posts"],
            first=timing["seconds_until_first_preload"],
            name="preload_memes",
        )

    # Bot routines

    def _botStartupRoutine(self, context: CallbackContext):
        """Sends a message to admins when the bot is started"""
        logging.info("Starting startup routine...")
        self._setMemesRoutineInterval()

        message = "*Bot started!*"
        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

        logging.info("Startup routine completed.")

    def _botClearRoutine(self, context: CallbackContext):
        # TODO this has to be tested
        """Routines that handles the removal of old posts"""
        logging.info("Starting clear routine...")

        message = "*Clear day routine!*"
        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=True,
            )

        posts, fingerprints = self._database.clean()
        logging.info(
            f"Removed {posts} post and {fingerprints} fingerprint documents "
            "of old data removed from database."
        )

        logging.info("New day routine completed.")

    def _botPreloadmemeRoutine(self, _: CallbackContext):
        """Routine that preloads memes and puts them into queue"""
        logging.info("Preload memes routine begins")
        # load url from reddit

        if not self._queue:
            # no urls on reddit, fetching a new one

            old_ids = self._database.getOldIds()
            old_hashes = self._database.getOldHashes()
            old_urls = self._database.getOldUrls()

            to_check = [
                p
                for p in self._reddit.fetch()
                if p.id not in old_ids and p.url not in old_urls
            ]

            logging.info(f"Looking for a post between {len(to_check)} posts preloaded")

            for post in to_check:
                fingerprint = self._fingerprinter.fingerprint(post.url)
                self._database.addPostToDatabase(post=post, fingerprint=fingerprint)

                if any(
                    abs(fingerprint.hash - f) < self._settings["hash_threshold"]
                    for f in old_hashes
                ):
                    logging.info("Skipping. Too similar to past image.")
                    continue

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

    def _botSendmemeRoutine(self, context: CallbackContext):
        """Routine that send memes when it's time to do so"""
        logging.info("Sending memes routine begins.")

        if not self._queue:
            logging.error("Queue is empty. Aborting.")
            return

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        new_url = self._queue.pop(0).url
        logging.info(f"Sending image with url {new_url}.")

        count = 0
        max_retries = self._settings["max_retries"]
        while True:
            try:
                context.bot.send_photo(
                    chat_id=channel_name, photo=new_url, caption=caption
                )
                logging.info("Image sent.")
                break

            except Exception as e:
                count += 1
                logging.error(
                    "Cannot send photo. "
                    f"Error {e}. "
                    f"Try {count} of {max_retries}."
                )

                if count == max_retries:
                    logging.error("Could not send the image. Aborting.")
                    break

                sleep(10)

        logging.info("Sending memes routine completed.")

    def _botError(self, update, context):
        """Function that sends a message to admins whenever
        an error is raised"""
        message = "*ERROR RAISED*"
        # admin message
        for chat_id in self._settings["admins"]:
            context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
            )

        error_string = str(context.error)
        time_string = datetime.now().isoformat()

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
    def _botStartCommand(self, update, context):
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

    def _botResetCommand(self, update, context):
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

    def _botStopCommand(self, update, context):
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

    def _botStatusCommand(self, update, context):
        """Function handling status command"""
        # TODO clean this, maybe it's not really necessary
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            # first, post bot status
            message = "*Bot status*\n"
            message += f"Version: {self._version}"
            # every dump is replaced to escape the underscore

            # then, post telegram status
            message += "\n\n*Telegram status*\n"
            message += self._escapeMarkdown(
                ujson.dumps(self._settings, indent=4, sort_keys=True)
            )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botNextpostCommand(self, update, context):
        """Function handling nextpost command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            timing = self._calculateTiming()
            if timing["next_post_timestamp_no_preload"]:
                message = (
                    "_The next meme is scheduled for:_ "
                    f"{timing['next_post_timestamp_no_preload']}"
                )
            else:
                message = (
                    "_The bot hasn't completed it startup process yet. "
                    " Wait a few seconds!_"
                )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botQueueCommand(self, update, context):
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
                    self._database.addPostToDatabase(post=post, fingerprint=fingerprint)
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

    def _botSubredditsCommand(self, update, context):
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

    def _botPostsperdayCommand(self, update, context):
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
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
                    )
                    return

            self._setMemesRoutineInterval()
            # notify user
            message = f"_Number of posts per day:_ {self._settings['posts_per_day']}"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botCleanqueueCommand(self, update, context):
        """Function handling cleanqueue command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            self._queue = []
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botPingCommand(self, update, context):
        """Function handling ping command"""
        chat_id = update.effective_chat.id
        context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = "🏓 *PONG* 🏓"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def _botVersionCommand(self, update, context):
        """Function handling version command"""
        chat_id = update.effective_chat.id

        if self._isAdmin(chat_id):
            escaped_version = self._escapeMarkdown(self._version)
            message = "_Version:_ " f"{escaped_version}"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN
        )

    def start(self):
        """Function that starts the bot in all its components"""

        # create instances
        self._reddit = Reddit()
        self._database = Database()
        self._fingerprinter = Fingerprinter()

        # start the bot
        self._updater = Updater(self._settings["token"], use_context=True)
        self._dispatcher = self._updater.dispatcher
        self._jobqueue = self._updater.job_queue

        # this routine will notify the admins
        self._jobqueue.run_once(self._botStartupRoutine, when=1, name="startup_routine")

        # this routine will clean the posted list
        self._jobqueue.run_daily(
            self._botClearRoutine, time(0, 15, 0, 000000), name="new_day_routine"
        )

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

        self._dispatcher.add_handler(CommandHandler("version", self._botVersionCommand))

        # hidden command, not in list
        self._dispatcher.add_handler(CommandHandler("ping", self._botPingCommand))

        # this handler will notify the admins and the user if something went
        #   wrong during the execution
        self._dispatcher.add_error_handler(self._botError)

        self._updater.start_polling()
        logging.info("Bot running.")
        self._updater.idle()


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
