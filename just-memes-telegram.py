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
    wordstoskip - view and sets a list of words to skip
    toggleocr - toggles OCR on posts
    imagethreshold - set image threshold (0 completely disables image hashing)
    startdelay - set delay (minutes after midnight) to start posting
    cleanqueue - cleans queue
    cleanpostedlist - cleans list of posted memes
    status - show some infos about the bot
    version - show bot version
    '''

    def __init__(self):
        self._version = "1.8.1.3b"  # current bot version
        self._settings_path = "settings/settings.json"
        self._settings = []
        self._r = None
        self._send_memes_job = None

        self._loadSettings()

    # Private methods

    def _loadSettings(self):
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Telegram"]

    def _saveSettings(self):
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Telegram"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _escapeMarkdown(self, str):
        escaped = str.replace("_", "\\_")
        escaped = escaped.replace("*", "\\*")
        escaped = escaped.replace("[", "\\[")
        escaped = escaped.replace("`", "\\`")

        return escaped

    def _calculateTiming(self):
        """ Calculates seconds between posts and until next post"""

        self._minutes_between_messages = int(
            24 * 60 / self._posts_per_day
        )

        delay_minutes = timedelta(
            minutes=self._start_delay
        )

        midnight = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + delay_minutes

        now = datetime.now().replace(second=0, microsecond=0)

        # Time between memes has been already calculated in loadSettings
        minutes_between_messages = timedelta(
            minutes=self._minutes_between_messages
        )

        # Initialize the loop to add an interval until we are in the future
        next_post = midnight
        while (next_post <= now):
            next_post += minutes_between_messages

        # To avoid rounding errors we recalculate the current time
        now = datetime.now()
        seconds_until_next_post = (next_post - now).seconds

        return {
            "seconds_between": 60 * self._minutes_between_messages,
            "seconds_until": seconds_until_next_post,
            "timestamp": next_post.isoformat()
        }

    def _setMemesRoutineInterval(self):
        timing = self._calculateTiming()

        # we remove already started jobs from the schedule
        # (this happens when we change the number of posts per day)

        if (self._send_memes_job):
            self._send_memes_job.schedule_removal()

        # set new routine
        self._send_memes_job = self._jobqueue.run_repeating(
            self._botSendmemeRoutine,
            interval=timing["seconds_between"],
            first=timing["seconds_until"],
            name="send_memes"
        )

    # Bot routines

    def _botStartupRoutine(self, context: CallbackContext):
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
        # sourcery skip: class-extract-method
        message = "*Clear day routine!*"
        for chat_id in self._admins:
            context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
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
                parse_mode=ParseMode.MARKDOWN
            )

        logging.info("New day routine ended")

    def _botSendmemeRoutine(self, context: CallbackContext):
        logging.info("Sending memes routine begins")

        # load url from reddit
        new_url = self._reddit.new_url

        if not new_url:
            # no urls on reddit, fetching a new one
            self._reddit.fetch()
            logging.info("Posts found")
            # adds the photo to bot queue so we can use this later
            new_url = self._reddit.new_url
        else:
            logging.info("Loading meme from queue")

        # it's time to post a meme
        channel_name = self._settings["channel_name"]
        caption = self._settings["caption"]
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

                if (count == max_retries):
                    logging.error("Could not send the image. Aborting.")
                    break

                sleep(10)

        logging.info("Sending memes routine completed")

    def _botError(self, update, context):
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
                        "_Pass the links as arguments to set them_"
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
        chat_id = update.effective_chat.id

        if chat_id in self._admins:
            if len(context.args) == 0:
                subreddits = self._reddit.subreddits
                subreddits_list = '\n'.join(subreddits).replace('_', '\\_')
                message = (
                    "_Current subreddits:_\n"
                    f"{subreddits_list}"
                    "\n_Pass the subreddit list as arguments to set them_"
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
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                posts_per_day = self._posts_per_day

                message = (
                    f"_Posts per day:_ {posts_per_day}\n"
                    f"_Pass the number of posts per day as arguments "
                    "to set a new value_"
                )
            else:
                try:
                    posts_per_day = int(context.args[0])
                except ValueError:
                    message = "_The argument provided is not a number_"
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return

                self._posts_per_day = posts_per_day
                self._setMemesRoutineInterval()
                self._saveSettings()

            message = f"_Number of posts per day:_ {posts_per_day}"
        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botWordstoskipCommand(self, update, context):
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                words_to_skip = self._reddit.wordstoskip
                words_list = "\n".join(words_to_skip).replace("_", "\\_")
                message = (
                    "_Current words to be skipped:_\n"
                    f"{words_list}"
                    "\n_Pass the words to skip as arguments to set them "
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
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                message = (
                    f"_Current hash threshold:_ {self._reddit.threshold}"
                    f"\n_Pass the threshold as arguments to set a new value_"
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
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            if len(context.args) == 0:
                start_delay = self._start_delay
                message = (
                    f"_Current start delay:_ {start_delay}\n"
                    f"_Pass the threshold as arguments to set a new value_"
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

    def _botCleanpostedlistCommand(self, update, context):
        chat_id = update.effective_chat.id
        if chat_id in self._admins:
            removed = self._reddit.cleanPosted()
            discarded = self._reddit.cleanDiscarded()

            message = (
                f"{removed} old posts were removed!\n"
                f"{discarded} discarded posts were removed!"
            )

        else:
            message = "*This command is for admins only*"

        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botPingCommand(self, update, context):
        chat_id = update.effective_chat.id
        context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = "🏓 *PONG* 🏓"
        context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    def _botVersionCommand(self, update, context):
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

    def start(self):
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

        self._dispatcher.add_handler(
            CommandHandler(
                "cleanpostedlist",
                self._botCleanpostedlistCommand
            )
        )

        # hidden command, not in list
        self._dispatcher.add_handler(
            CommandHandler(
                "ping",
                self._botPingCommand
            )
        )

        self._dispatcher.add_handler(
            CommandHandler("version", self._botVersionCommand)
        )

        # this handler will notify the admins and the user if something went
        #   wrong during the execution
        self._dispatcher.add_error_handler(self._botError)

        self._updater.start_polling()
        logging.info("Bot running")
        self._updater.idle()

    @ property
    def _posts_per_day(self):
        return self._settings["posts_per_day"]

    @ _posts_per_day.setter
    def _posts_per_day(self, value):
        self._settings["posts_per_day"] = value
        self._saveSettings()

    @ property
    def _start_delay(self):
        return self._settings["start_delay"]

    @ _start_delay.setter
    def _start_delay(self, value):
        self._settings["start_delay"] = value
        self._saveSettings()

    @ property
    def _admins(self):
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
