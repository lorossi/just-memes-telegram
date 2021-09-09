import os
import sys
import ujson
import logging
from time import sleep
from datetime import datetime, time, timedelta
from telegram import ParseMode, ChatAction
from telegram.ext import Updater, CommandHandler, CallbackContext

from reddit import Reddit


class Telegram:
    # Class that handles all the Telegram stuff
    '''
    BOTFATHER commands description
    start - view start message
    reset - reloads the bot
    stop - stops the bot
    nextpost - show at what time the next post is
    queue - view queue and add image(s) url(s) to queue
    subreddits - views and sets source subreddits
    postsperday - views and sets number of posts per day
    preloadtime - vies and sets preload time
    wordstoskip - view and sets a list of words to skip
    toggleocr - toggles OCR on posts
    imagethreshold - set image threshold (0 completely disables image hashing)
    startdelay - set delay (minutes after midnight) to start posting
    cleanqueue - cleans queue
    status - show some infos about the bot
    channelname - show the destination channel name
    '''

    def __init__(self):
        self._version = "1.8.2"  # current bot version
        self._settings_path = "settings/settings.json"
        self._settings = []
        self._r = None
        self._send_memes_job = None
        self._preload_memes_job = None

        self._loadSettings()

    # Private methods

    def _loadSettings(self):
        """ Loads settings from file """
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Telegram"]

    def _saveSettings(self):
        """ Saves settings to file """
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Telegram"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _escapeMarkdown(self, str):
        """ Replaces markdown delimites with escaped ones """
        to_replace = ["_", "*", "[", "`"]
        for replace in to_replace:
            str = str.replace(replace, f"\\{replace}")
        return str

    def _calculateTiming(self):
        """ Calculates seconds between posts and until next post"""

        # calculation of time between messages
        self._minutes_between_messages = int(
            24 * 60 / self._posts_per_day
        )
        # convert start delay into timedelta
        delay_minutes = timedelta(
            minutes=self._start_delay
        )
        # convert preload time into timedelta
        preload_time = timedelta(
            minutes=self._preload_time
        )
        # convert minutes_between_messages time into timedelta
        # minutes_between_messages was calculated before
        minutes_between_messages = timedelta(
            minutes=self._minutes_between_messages
        )

        # starting time
        midnight = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + delay_minutes
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

    def _setMemesRoutineInterval(self):
        """ Create routine to send memes """
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
            name="send_memes"
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
            name="preload_memes"
        )

    # Bot routines

    def _botStartupRoutine(self, context: CallbackContext):
        """ Sends a message to admins when the bot is started """
        self._setMemesRoutineInterval()

        message = "*Bot started!*"
        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )

        logging.info("Startup routine completed")

    def _botClearRoutine(self, context: CallbackContext):
        """ Routines that handles the removal of old posts """
        # sourcery skip: class-extract-method
        message = "*Clear day routine!*"
        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=True
            )

        removed = self._reddit.cleanPosted()
        discarded = self._reddit.cleanDiscarded()

        message = ""
        if removed == 0:
            message += "No old posts have been removed.\n"
        elif removed == 1:
            message += "1 old post has been removed.\n"
        else:
            message += f"{removed} old posts were removed!\n"

        if discarded == 0:
            message += "No discarded posts have been removed.\n"
        elif discarded == 1:
            message += "1 discarded post has been removed.\n"
        else:
            message += f"{discarded} discarded posts were removed!\n"

        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=True
            )

        logging.info("New day routine ended")

    def _botPreloadmemeRoutine(self, _: CallbackContext):
        """ Routine that preloads memes and puts them into queue """
        logging.info("Preload memes routine begins")
        # load url from reddit

        if not self._reddit.meme_loaded:
            # no urls on reddit, fetching a new one
            self._reddit.fetch()
            # adds the photo to bot queue so we can use this later
            logging.info("Posts found")
        else:
            logging.info("Loading meme from queue")

        logging.info("Preload memes routine ended")

    def _botSendmemeRoutine(self, context: CallbackContext):
        """ Routine that send memes when it's time to do so """
        logging.info("Sending memes routine begins")

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
        new_url = self._reddit.meme_url
        logging.info(f"Sending image with url {new_url}")

        count = 0
        max_retries = self._settings["max_retries"]
        while True:
            try:
                context.bot.send_photo(
                    chat_id=channel_name,
                    photo=new_url,
                    caption=caption
                )
                logging.info("Image sent")
                break

            except Exception as e:
                count += 1
                logging.error(
                    "Cannot send photo. "
                    f"Error {e}. "
                    f"Try {count} of {max_retries}"
                )

                if count == max_retries:
                    logging.error("Could not send the image. Aborting.")
                    break

                sleep(10)

        logging.info("Sending memes routine completed")

    def _botError(self, update, context):
        """ Function that sends a message to admins whenever
        an error is raised """
        message = "*ERROR RAISED*"
        # admin message
        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )

        error_string = str(context.error)
        time_string = datetime.now().isoformat()

        message = (
            f"Error at time: {time_string}\n"
            f"Error raised: {error_string}\n"
            f"Update: {update}"
        )

        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                disable_web_page_preview=True
            )

        # logs to file
        logging.error(f"Update {update} caused error {context.error}")

    # Bot commands
    def _botStartCommand(self, update, context):
        """ Function handling start command """
        chat_id = update.effective_chat.id

        message = (
            f"*Welcome in the Just Memes backend bot*\n"
            f"*This bot is used to manage the Just Memes meme channel*\n"
            f"_Join us at_ {self._settings['channel_name']}"
        )
        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botResetCommand(self, update, context):
        """ Function handling reset command """
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            message = "_Resetting..._"

            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )

            logging.warning("Resetting")
            os.execl(sys.executable, sys.executable, * sys.argv)
        else:
            message = "*This command is for admins only*"
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )

    def _botStopCommand(self, update, context):
        """ Function handling stop command """
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            message = "_Bot stopped_"
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            self._updater.stop()
            logging.warning("Bot stopped")
            os._exit()
        else:
            message = "*This command is for admins only*"
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )

    def _botStatusCommand(self, update, context):
        """ Function handling status command """
        # sourcery skip: extract-method
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            # first, post bot status
            message = "*Bot status*\n"
            message += f"Version: {self._version}"
            # every dump is replaced to escape the underscore

            # then, post telegram status
            message += "\n\n*Telegram status*\n"
            message += self._escapeMarkdown(
                ujson.dumps(
                    self._settings,
                    indent=4,
                    sort_keys=True
                )
            )

            # then, next post status
            message += "\n\n*Next post status*\n"
            message += self._escapeMarkdown(
                ujson.dumps(
                    self._calculateTiming(),
                    indent=4,
                    sort_keys=True
                )
            )

            # then, post reddit status
            message += "\n\n*Reddit status*\n"
            message += self._escapeMarkdown(
                ujson.dumps(
                    self._reddit.settings,
                    indent=4,
                    sort_keys=True
                )
            )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botNextpostCommand(self, update, context):
        logging.info("Called next post command")
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            timing = self._calculateTiming()
            if timing["timestamp"]:
                message = (
                    "_The next meme is scheduled for:_\n"
                    f"{timing['timestamp']}\n"
                    f"_That is_ {timing['seconds_until']} "
                    "_seconds from now._\n"
                )
            else:
                message = (
                    "_The bot hasn't completed it startup process yet. "
                    " Wait a few seconds!_"
                )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botQueueCommand(self, update, context):
        """ Function handling queue command """
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            # check if any arg has been passed
            if len(context.args) == 0:
                if len(self._reddit.queue) > 0:
                    queue = ujson.dumps(
                        self._reddit.queue,
                        indent=2,
                        sort_keys=True
                    )

                    message = (
                        f"_Current message queue:_\n"
                        f"{queue}"
                    )
                else:
                    message = (
                        "*The queue is empty*\n"
                        "_Pass the links as argument to set them_"
                    )
            else:
                for url in context.args:
                    self._reddit.addPost(url)

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
            disable_web_page_preview=True
        )

    def _botSubredditsCommand(self, update, context):
        """ Function handling subreddits command """
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            if len(context.args) == 0:
                subreddits = self._reddit.subreddits
                subreddits_list = '\n'.join(subreddits).replace('_', '\\_')
                message = (
                    "_Current subreddits:_\n"
                    f"{subreddits_list}"
                    "\n_Pass the subreddit list as argument to set them_"
                )

            else:
                self._reddit.subreddits = context.args
                subreddits_list = '\n'.join(context.args).replace('_', '\\_')

                message = (
                    "_New subreddit list:_\n"
                    f"{subreddits_list}"
                )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botPostsperdayCommand(self, update, context):
        """ Function handling postsperday command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                message = (
                    f"_Posts per day:_ {self._posts_per_day}\n"
                    "_Pass the number of posts per day as argument "
                    "to set a new value_"
                )
            else:
                try:
                    self._posts_per_day = int(context.args[0])
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return

            self._setMemesRoutineInterval()
            # notify user
            message = f"_Number of posts per day:_ {self._posts_per_day}"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botPreloadtimeCommand(self, update, context):
        """ Function handling preloadtime command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                message = (
                    f"_Preload time:_ {self._preload_time} minutes\n"
                    "_Pass the number of posts per day as argument "
                    "to set a new value_"
                )
            else:
                try:
                    self._preload_time = int(context.args[0])
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return

            # save value

            self._setMemesRoutineInterval()
            # notify user
            message = f"_Preload time:_ {self._preload_time} minutes"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botWordstoskipCommand(self, update, context):
        """ Function handling wordstoskip command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                words_to_skip = self._reddit.wordstoskip
                words_list = "\n".join(words_to_skip).replace("_", "\\_")
                message = (
                    "_Current words to be skipped:_\n"
                    f"{words_list}"
                    "\n_Pass the words to skip as argument to set them "
                    "(escape spaces with backslash)_"
                )

            else:
                # update the list of words to skip
                self._reddit.wordstoskip = [
                    arg.replace('\\', ' ') for arg in context.args
                ]

                words_list = words_list = "\n".join(
                    self._reddit.wordstoskip).replace("_", "\\_")

                message = (
                    "_New list of words to skip:_\n"
                    f"{words_list}"
                )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botToggleocrCommand(self, update, context):
        """ Function handling toggleocr command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            self._reddit.ocr = not self._reddit.oc
            message = (
                "_Ocr is now:_ "
                f"{'Disabled' if self._reddit.ocr else 'Enabled'}"
            )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botImageThresholdCommand(self, update, context):
        """ Function handling threshold command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                message = (
                    f"_Current hash threshold:_ {self._reddit.threshold}"
                    f"\n_Pass the threshold as argument to set a new value_"
                )
            else:
                try:
                    image_threshold = int(context.args[0])
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )

                    return

                self._reddit.threshold = image_threshold
                message = (
                    f"_Image threshold:_ "
                    f"{self._reddit.threshold}"
                )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botStartdelayCommand(self, update, context):
        """ Function handling startdelay command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                start_delay = self._start_delay
                message = (
                    f"_Current start delay:_ {start_delay}\n"
                    f"_Pass the threshold as argument to set a new value_"
                )
            else:
                try:
                    start_delay = int(context.args[0])
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return

                self._start_delay = start_delay
                self._calculateTiming()

                message = (
                    "_Start delay:_ "
                    f"{self._settings['start_delay']}"
                )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botCleanqueueCommand(self, update, context):
        """ Function handling cleanqueue command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            self._reddit.cleanQueue()
            message = "_The queue has been cleaned_"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botPingCommand(self, update, context):
        """ Function handling ping command """
        chat_id = update.effective_chat.id
        context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = "🏓 *PONG* 🏓"
        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botVersionCommand(self, update, context):
        """ Function handling version command """
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            escaped_version = self._escapeMarkdown(self._version)
            message = (
                "_Version:_ "
                f"{escaped_version}"
            )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botChannelnameCommand(self, update, context):
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            escaped_name = self._escapeMarkdown(self._channel_name)
            message = (
                "_Channel name:_ "
                f"{escaped_name}"
            )
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def start(self):
        """ Function that starts the bot in all its components """

        # create reddit object
        self._reddit = Reddit()

        # start the bot
        self._updater = Updater(
            self._settings["token"],
            use_context=True
        )
        self._dispatcher = self._updater.dispatcher
        self._jobqueue = self._updater.job_queue

        # this routine will notify the admins
        self._jobqueue.run_once(
            self._botStartupRoutine,
            when=1,
            name="startup_routine"
        )

        # this routine will clean the posted list
        self._jobqueue.run_daily(
            self._botClearRoutine,
            time(0, 15, 0, 000000),
            name="new_day_routine"
        )

        # these are the handlers for all the commands
        self._dispatcher.add_handler(
            CommandHandler("start", self._botStartCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler("reset", self._botResetCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler("stop", self._botStopCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler("status", self._botStatusCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler("nextpost", self._botNextpostCommand)
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "queue",
                self._botQueueCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "subreddits",
                self._botSubredditsCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "postsperday",
                self._botPostsperdayCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "preloadtime",
                self._botPreloadtimeCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "wordstoskip",
                self._botWordstoskipCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "toggleocr",
                self._botToggleocrCommand,
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "imagethreshold",
                self._botImageThresholdCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "startdelay",
                self._botStartdelayCommand,
                pass_args=True
            )
        )

        self._dispatcher.add_handler(
            CommandHandler(
                "cleanqueue",
                self._botCleanqueueCommand
            )
        )

        # hidden command, not in list
        self._dispatcher.add_handler(
            CommandHandler(
                "ping",
                self._botPingCommand
            )
        )

        # hidden command, not in list
        self._dispatcher.add_handler(
            CommandHandler(
                "version",
                self._botVersionCommand
            )
        )

        self._dispatcher.add_handler(
            CommandHandler("channelname", self._botChannelnameCommand)
        )

        # this handler will notify the admins and the user if something went
        #   wrong during the execution
        self._dispatcher.add_error_handler(self._botError)

        self._updater.start_polling()
        logging.info("Bot running")
        self._updater.idle()

    @ property
    def _posts_per_day(self):
        """ Getter for the number of posts per day """
        return self._settings["posts_per_day"]

    @ _posts_per_day.setter
    def _posts_per_day(self, value):
        """ Setter for the number of posts per day """
        self._settings["posts_per_day"] = value
        self._saveSettings()

    @ property
    def _start_delay(self):
        """ Getter for the start delay """
        return self._settings["start_delay"]

    @ _start_delay.setter
    def _start_delay(self, value):
        """ Setter for the start delay """
        self._settings["start_delay"] = value
        self._saveSettings()

    @ property
    def _preload_time(self):
        """ Getter for the preload time """
        return self._settings["preload_time"]

    @ _preload_time.setter
    def _preload_time(self, value):
        """ Setter for the preload time """
        self._settings["preload_time"] = value
        self._saveSettings()

    @ property
    def _admins(self):
        """ Getter for the admins list """
        return self._settings["admins"]

    @ property
    def _channel_name(self):
        return self._settings["channel_name"]


def main():
    logging.basicConfig(filename=__file__.replace(".py", ".log"),
                        level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        filemode="w")

    logging.info("Script started")

    t = Telegram()
    logging.info("Telegram initialized")
    t.start()


if __name__ == '__main__':
    main()
