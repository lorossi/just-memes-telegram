import os
import sys
import json
import time
import string
import logging
import datetime
import requests
import imagehash
import pytesseract
from PIL import Image
from telegram import ParseMode, ChatAction
from telegram.ext import Updater, CommandHandler, CallbackContext

from reddit import Reddit


class Telegram:
    # Class that handles all the Telegram stuff
    ''' BOTFATHER commands description
    start - view start message
    reset - reloads the bot
    stop - stops the bot
    nextpost - show at what time the next post is
    queue - view queue  and add image(s) url(s) to queue
    subreddits - views and sets source subreddits
    postsperday - views and sets number of posts per day
    wordstoskip - view and sets a list of words to skip
    toggleocr - toggles OCR on posts
    imagethreshold - set image threshold (0 completely disables image hashing)
    startdelay - set delay (minutes after midnight) to start posting
    cleanqueue - cleans queue
    cleanpostedlist - cleans list of posted memes
    botstatus - show all the bot stats
    '''

    def __init__(self):
        self.time_format = '%Y/%m/%d %H:%M:%S'
        self.version = "1.6"  # current bot version
        self.send_memes_job = None

    def loadSettings(self, filename):
        self.settings_path = filename

        with open(self.settings_path) as json_file:
            self.settings = json.load(json_file)["Telegram"]

        self.token = self.settings["token"]
        self.channel_name = self.settings["channel_name"]
        self.posts_per_day = self.settings["posts_per_day"]
        self.start_delay = self.settings["start_delay"]
        self.max_retries = self.settings["max_retries"]
        self.queue = self.settings["queue"]

        self.admins = self.settings["admins"]
        self.user_log_file = self.settings["users_log_file"]
        self.minutes_between_messages = 24 * 60 / self.posts_per_day

    def saveSettings(self):
        with open(self.settings_path) as json_file:
            old_settings = json.load(json_file)

        old_settings["Telegram"].update(self.settings)

        with open(self.settings_path, 'w') as outfile:
            json.dump(old_settings, outfile, indent=4)

    def calculateNextPost(self):
        # Calculates seconds between posts and until next post
        delay_minutes = datetime.timedelta(minutes=self.start_delay)
        midnight = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0) + delay_minutes
        now = datetime.datetime.now().replace(second=0, microsecond=0)

        # Time between memes has been already calculated in loadSettings
        minutes_between_messages = datetime.timedelta(
            minutes=self.minutes_between_messages)

        # Initialize the loop to add an interval until we are in the future
        next_post = midnight
        while (next_post <= now):
            next_post += minutes_between_messages

        # To avoid rounding errors we recalculate the current time
        now = datetime.datetime.now()
        seconds_until_next_post = (next_post - now).seconds
        seconds_between_messages = 60 * self.minutes_between_messages
        next_post_string = next_post.strftime(self.time_format)
        return (seconds_between_messages, seconds_until_next_post, next_post, next_post_string)

    def setMemesRoutineInterval(self):
        interval, first, next_post, timestamp = self.calculateNextPost()

        # we remove already started jobs from the schedule (this happens when we change the number of posts per day)
        if (self.send_memes_job):
            self.send_memes_job.schedule_removal()

        self.send_memes_job = self.jobqueue.run_repeating(
            send_memes, interval=interval, first=first, name="send_memes")

    def start(self):
        self.updater = Updater(self.token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.jobqueue = self.updater.job_queue

        self.jobqueue.run_once(startup, when=1, name="startup")
        self.jobqueue.run_daily(new_day, datetime.time(
            0, 15, 0, 000000), name="new_day")

        self.dispatcher.add_handler(CommandHandler('start', start))
        self.dispatcher.add_handler(
            CommandHandler('reset', reset))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler('stop', stop))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'botstatus', botstatus))  # DANGEROUS

        self.dispatcher.add_handler(CommandHandler(
            'nextpost', nextpost, pass_args=True))  # DANGEROUS
        self.dispatcher.add_handler(
            CommandHandler('queue', queue, pass_args=True))

        self.dispatcher.add_handler(CommandHandler(
            'subreddits', subreddits, pass_args=True))
        self.dispatcher.add_handler(CommandHandler(
            'postsperday', postsperday, pass_args=True))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'wordstoskip', wordstoskip, pass_args=True))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'toggleocr', toggleocr, pass_args=True))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'imagethreshold', imagethreshold, pass_args=True))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'startdelay', start_delay, pass_args=True))  # DANGEROUS

        self.dispatcher.add_handler(CommandHandler(
            'cleanqueue', cleanqueue))  # DANGEROUS
        self.dispatcher.add_handler(CommandHandler(
            'cleanpostedlist', cleanpostedlist))  # DANGEROUS

        self.dispatcher.add_handler(CommandHandler('ping', ping))  # Kinda ok

        self.dispatcher.add_error_handler(error)
        self.updater.start_polling()

    def idle(self):
        self.updater.idle()

    def updateQueue(self, post):
        now = datetime.datetime.now()  # current date and time
        timestamp = now.strftime("%Y%m%d")
        self.queue.append(post)
        self.settings["queue"] = self.queue
        # self.saveSettings()

    def showStatus(self):
        return {
            "queue": self.queue,
            "caption": self.channel_name,
            "channel_name": self.channel_name,
            "admins": self.admins,
            "posts_per_day": self.posts_per_day,
            "minutes_between_messages": self.minutes_between_messages,
            "start_delay": self.start_delay,
            "max_retries": self.max_retries,
            "time_format": self.time_format,
            "version": self.version
        }

    def setPostsPerDay(self, posts_per_day):
        self.posts_per_day = posts_per_day
        self.minutes_between_messages = 24 * 60 / self.posts_per_day
        #self.next_post = None
        self.settings["posts_per_day"] = self.posts_per_day
        # self.saveSettings()

    def popQueue(self):
        self.queue.pop(0)
        #queue_string = json.dumps(self.queue)
        self.settings["queue"] = self.queue
        # self.saveSettings()

    def cleanQueue(self):
        self.queue = []
        self.settings["queue"] = []
        # self.saveSettings()

    def setStartDelay(self, start_delay):
        self.start_delay = start_delay
        self.settings["start_delay"] = start_delay

    def logUser(self, user_dict):
        if (os.path.exists(self.user_log_file)):
            with open(self.user_log_file) as json_file:
                all_user_data = json.load(json_file)
        else:
            open(self.user_log_file, "w+")
            all_user_data = []

        not_found = True
        for d in all_user_data:
            if d["chat_id"] == user_dict["chat_id"]:
                d.update(user_dict)
                not_found = False
                break

        if not_found:
            all_user_data.append(user_dict)

        with open(self.user_log_file, 'w') as outfile:
            json.dump(all_user_data, outfile, indent=4)


def startup(context: CallbackContext):
    status = t.showStatus()
    t.setMemesRoutineInterval()
    message = "*Bot started!*"
    for chat_id in status["admins"]:
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
    logging.info("Startup routine completed")


def send_memes(context: CallbackContext):
    logging.info("Sending memes routine begins")
    status = t.showStatus()
    if len(status["queue"]) == 0:
        to_post = r.fetch()
        logging.info("Posts found")
        # adds the photo to bot queue so we can use this later
        t.updateQueue(to_post)
        t.saveSettings()
    else:
        logging.info("Loading meme from queue")

    status = t.showStatus()
    # it's time to post a meme
    channel_name = status["channel_name"]
    url = status["queue"][0]["url"]
    caption = status["caption"]
    logging.info("Sending image with url %s", url)

    # fix this it's ugly
    count = 0
    max_retries = status["max_retries"]
    while True:
        try:
            context.bot.send_photo(chat_id=channel_name,
                                   photo=url, caption=caption)
            logging.info("Image sent")
            break
        except Exception as e:
            count += 1
            logging.error("Cannot send photo. Error %s. Try %s of %s", str(
                e), str(count), str(max_retries))
            if (count == max_retries):
                logging.error("Could not send the image. Aborting.")
                break
            time.sleep(30)
            pass

    t.saveSettings()
    r._updatePosted()
    r._updateDiscarded()
    # we remove rhe last posted link from queue
    t.popQueue()
    t.saveSettings()
    logging.info("Sending memes routine completed")


def new_day(context: CallbackContext):
    status = t.showStatus()

    message = "*New day routine!*"
    for chat_id in status["admins"]:
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    removed = r.cleanPosted()
    discarded = r.cleanDiscarded()

    message = (
        f"{removed} old posts were removed!\n"
        f"{discarded} discarded posts were removed!"
    )

    for chat_id in status["admins"]:
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    logging.info("New day routine ended")


def error(update, context):
    """Log Errors caused by Updates."""
    # logging.error(context.error)
    status = t.showStatus()

    for chat_id in status["admins"]:
        message = "*ERROR RAISED*"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    error_string = str(context.error).replace("_", "\\_")  # MARKDOWN escape
    message = (
        f"Error at time: {datetime.datetime.now().strftime(status['time_format'])}\n"
        f"Error raised: {error_string}\n"
        f"Update: {update}"
    )

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message)

    logging.error('Update "%s" caused error "%s"', update, context.error)


def start(update, context):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username
    name = update.effective_chat.first_name
    status = t.showStatus()

    user_dict = {
        "username": username,
        "name": name,
        "chat_id": chat_id,
        "time": datetime.datetime.now().strftime(status['time_format']),
        "action": "start"
    }

    t.logUser(user_dict)

    message = (
        f"*Welcome in the Just Memes backend bot*\n"
        f"*This bot is used to manage the Just Memes meme channel*\n"
        f"_Join us at_ " + status["channel_name"]
    )
    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def reset(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "_Resetting..._"
        t.saveSettings()

        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
        logging.warning("Resetting")
        os.execl(sys.executable, sys.executable, * sys.argv)
    else:
        message = "*This command is for moderators only*"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def stop(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "_Bot stopped_"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
        t.saveSettings()
        t.updater.stop()
        logging.warning("Bot stopped")
        os._exit()
        exit()
    else:
        message = "*This command is for moderators only*"
        context.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def botstatus(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "*Bot status*\n"
        message += "_Telegram Status_\n"
        for key, value in status.items():
            message += "•*" + str(key) + ":* "
            message += "" + str(value) + "\n"

        status = r.showStatus()
        message += "\n_Reddit Status_\n"
        for key, value in status.items():
            message += "•*" + str(key) + ":* "
            message += "" + str(value) + "\n"

    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def nextpost(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    t.calculateNextPost()

    if chat_id in status["admins"]:
        next_post_timestamp = t.calculateNextPost()[3]
        if next_post_timestamp:
            #message = "_The next meme is scheduled for:_\n"
            #message += next_post_timestamp
            message = f"_The next meme is scheduled for:_\n{next_post_timestamp}"
        else:
            message = "_The bot hasn't completed it startup process yet. Wait a few seconds!_"
    else:
        message = "*This command is for moderators only*"
    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def toggleocr(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        status = r.showStatus()
        r.toggleOcr()
        r._saveSettings()
        message = f"_Ocr is now:_ {'Disabled' if status['ocr'] else 'Enabled'}"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def imagethreshold(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        status = r.showStatus()
        if len(context.args) == 0:
            image_threshold = status["hash_threshold"]
            message = (
                f"_Current hash threshold:_ {image_threshold}"
                f"\n_Pass the threshold as arguments to set a new value_"
            )
        else:
            try:
                image_threshold = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            r.setThreshold(image_threshold)
            r._saveSettings()

            status = r.showStatus()

            message = f"_Image threshold:_ {status['image_threshold']}"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def start_delay(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        status = r.showStatus()
        if len(context.args) == 0:
            start_delay = status["hash_threshold"]
            message = (
                f"_Current start delay:_ {str(start_delay)}\n"
                f"_Pass the threshold as arguments to set a new value_"
            )
        else:
            try:
                start_delay = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            t.setStartDelay(start_delay)
            t.calculateNextPost()
            t.saveSettings()

            status = t.showStatus()
            message = "_Start delay:_ "
            message += str(status["start_delay"])
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def cleanqueue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        t.cleanQueue()
        t.saveSettings()
        message = "_The queue has been cleaned_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def queue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            if len(status["queue"]) > 0:
                message = (
                    f"_Current message queue:_\n"
                    f"{json.dumps(status['queue'], indent=4, sort_keys=True)}"
                )
            else:
                message = (
                    f"*The queue is empty*\n"
                    f"_Pass the links as arguments to set them_"
                )
        else:
            now = datetime.datetime.now()  # current date and time
            timestamp = now.strftime("%Y%m%d")

            for arg in context.args:
                fingerprint = image_fingerprint(arg)

                to_post = {
                    "id": None,
                    "url": str(arg),
                    "subreddit": None,
                    "title": None,
                    "caption": fingerprint["caption"],
                    "hash": fingerprint["string_hash"],
                    "timestamp": timestamp
                }

                t.updateQueue(to_post)
                t.saveSettings()
                r._updatePosted(to_post)

            message = (
                f"_Image(s) added to queue_\n"
                f"_Use /queue to check the current queue_"
            )
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def subreddits(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            subreddits = r.showStatus()["subreddits"]
            message = "_Current subreddits:_\n•"
            message += "\n•".join(subreddits).replace("_", "\\_")
            message += "\n_Pass the subreddit list as arguments to set them_"
        else:
            r.setSubreddits(context.args)
            r._saveSettings()

            message = "_New subreddit list:_\n•"
            message += "\n•".join(context.args).replace("_", "\\_")
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def postsperday(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            posts_per_day = status["posts_per_day"]

            message = (
                f"_Posts per day:_ {posts_per_day}\n"
                f"_Pass the number of posts per day as arguments to set a new value_"
            )
        else:
            try:
                posts_per_day = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            t.setPostsPerDay(posts_per_day)
            t.setMemesRoutineInterval()
            t.saveSettings()

            message = f"_Number of posts per day:_ {posts_per_day}"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def wordstoskip(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            words_to_skip = r.showStatus()["words_to_skip"]
            message = "_Current words to be skipped:_\n•"
            message += "\n•".join(words_to_skip).replace("_", "\\_")
            message += "\n_Pass the words to skip as arguments to set them (escape spaces with backslash)_"
        else:
            cleaned_args = []
            for arg in context.args:
                cleaned_args.append(arg.replace('\\', ' '))

            r.setWordstoskip(cleaned_args)
            r._saveSettings()
            message = "_New list of words to skip:_\n•"
            message += "\n•".join(cleaned_args).replace("_", "\\_")
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def cleanpostedlist(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        removed = r.cleanPosted()
        discarded = r.cleanDiscarded()

        message = (
            f"{removed} old posts were removed!\n"
            f"{discarded} discarded posts were removed!"
        )

    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def ping(update, context):
    chat_id = update.effective_chat.id
    context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    message = "🏓 *PONG* 🏓"  # this MIGHT cause some issues...
    context.bot.send_message(
        chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)


def image_fingerprint(url, hash=True, ocr=True):
    try:
        r = requests.get(url, stream=True)
        r.raw.decode_content = True  # handle spurious Content-Encoding
        # Open it in PIL
        im = Image.open(r.raw)
        # Hash it
        if hash:
            hash = imagehash.average_hash(im)
        else:
            hash = None
        # OCR it
        if ocr:
            unicode_caption = pytesseract.image_to_string(im).lower()
            if unicode_caption.strip() == "":
                caption = ""
            else:
                printable = set(string.printable)
                caption = ''.join(
                    filter(lambda x: x in printable, unicode_caption))
        else:
            caption = None
        # close the image
        im.close()
    except Exception as e:
        logging.error("ERROR while fingerprinting %s %s", e, url)
        return None

    return {
        "hash": hash,
        "string_hash": str(hash),
        "caption": caption
    }


settings_file = "settings/settings.json"
logging_path = "logs/memes_only_telegram.log"

logging.basicConfig(filename=logging_path, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s', filemode="w")
#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

logging.info("Script started")
r = Reddit()
r._loadSettings(settings_file)
r._login()
logging.info("Reddit initialized")

t = Telegram()
t.loadSettings(settings_file)
logging.info("Telegram initialized")

t.start()
logging.info("Bot running")
t.idle()
