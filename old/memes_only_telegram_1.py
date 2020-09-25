import requests
import json
from datetime import datetime, timedelta
import os
import configparser
from telegram import ParseMode
from telegram.ext import Updater, CommandHandler, JobQueue, CallbackContext
import logging
import time
import sys
import praw


class Reddit:
    def __init__(self, debug=False):
        self.debug = debug
        self.after = None
        self.posts = []
        self.to_post = []
        self.posted_full_path = None
        self.settings = None
        self.settings_path = None

        #fallback values
        self.subreddits = ["dankmemes", "me_irl", "surrealmemes", "blursedimages"]
        self.request_limit = 20
        self.posted_folder = "posted"

    def showStatus(self):
        return {
            "request_limit" : self.request_limit,
            "subreddits" : self.subreddits,
            "posted_folder" : self.posted_folder
        }

    def loadSettings(self, filename):
        if not self.debug:
            self.settings_path = filename
        else:
            self.settings_path = filename + "_debug"

        self.settings = configparser.ConfigParser()
        self.settings.read(filename)
        self.id = self.settings["Reddit"]["id"]
        self.token = self.settings["Reddit"]["token"]
        self.request_limit = int(self.settings["Reddit"]["request_limit"])
        self.subreddits = self.settings["Reddit"]["subreddits"].split(",")
        self.posted_folder = self.settings["Reddit"]["posted_folder"]

    def login(self):
        self.reddit = praw.Reddit(client_id=self.id,
                     client_secret=self.token,
                     user_agent='PC')

    def saveSettings(self):
        with open(self.settings_path, 'w') as configfile:
            self.settings.write(configfile)

    def loadPosts(self):
        for submission in self.reddit.subreddit("+".join(self.subreddits)).hot(limit=self.request_limit):
            if not submission.selftext and not submission.stickied:
                self.posts.append(
                    {
                        "id" : submission.id,
                        "url" : submission.url
                    }
                )
        return True

    def findNew(self, num=1):
        now = datetime.now() # current date and time
        timestamp = now.strftime("%Y%m%d")
        #we get the location of the file that contains the memes that have already been posted

        self.posted_full_path = self.posted_folder + "/" + timestamp
        if (os.path.exists(self.posted_full_path)):
            ids = open(self.posted_full_path).read()
            self.posted_ids = ids.split("\n")
        else:
            open(self.posted_full_path, "w+")
            self.posted_ids = []

        self.to_post = []
        for post in self.posts:
            if post["id"] not in self.posted_ids:
                self.to_post = [post]
                self.after = None #we found a new post, so we can reset the after parameter
                if len(self.to_post) >= num:
                    return True
        return False

    def updateList(self):
        f = open(self.posted_full_path, "a")
        f.write(self.to_post[0]["id"])
        f.write("\n")
        f.close()

    def toPost(self, num=1):
        return self.to_post[0:num]

    def fetch(self):
        logging.info("Fetching new memes...")
        while not self.findNew():
            while not self.loadPosts():
                logging.info("Trying again in 10...")
                time.sleep(10)
        self.updateList()
        self.to_post = r.toPost()
        logging.info("Memes fetched")
        return self.to_post

    def setsubreddits(self, subreddits):
        self.subreddits = []
        for subreddit in subreddits:
            self.subreddits.append(subreddit)
        self.settings["Reddit"]["subreddits"] = ",".join(self.subreddits)
        self.saveSettings()

class Telegram:
    def __init__(self, debug=False):
        self.debug = debug
        self.last_posted = None
        self.settings = None
        self.time_format = '%Y/%m/%d %H:%M:%S'
        self.next_post = None

        #fallback values
        self.token = ""
        self.channel_name = ""
        self.posts_per_day = 10
        self.minutes_between_messages = 144
        self.posted_today = 0
        self.start_time = "00:00" #24 hour format HH:MM
        self.queue = [] #list of url to be posted
        self.admins = []
        self.user_log_file = "users.log"

    def loadSettings(self, filename):
        if not self.debug:
            self.settings_path = filename
        else:
            self.settings_path = filename + "_debug"

        self.settings = configparser.ConfigParser()
        self.settings.read(self.settings_path)

        self.token = self.settings["Telegram"]["Token"]
        self.channel_name = self.settings["Telegram"]["channel_name"]
        self.posts_per_day = int(self.settings["Telegram"]["posts_per_day"])

        self.start_time_string = self.settings["Telegram"]["start_time"].split(":")
        self.recalculateStartTime()

        last_posted_timestamp = self.settings["Telegram"]["last_posted"]
        if last_posted_timestamp:
            last_posted_struct = time.strptime(last_posted_timestamp, self.time_format)
            self.last_posted = datetime(*last_posted_struct[:6])

        next_post_timestamp = self.settings["Telegram"]["next_post"]
        if next_post_timestamp:
            next_post_struct = time.strptime(next_post_timestamp, self.time_format)
            self.last_posted = datetime(*next_post_struct[:6])

        if self.settings["Telegram"]["queue"] != "None":
            self.queue = self.settings["Telegram"]["queue"].split(",")
            if self.queue == [""]:
                self.queue = []

        self.admins = [int(x) for x in self.settings["Telegram"]["admins"].split(",") if x]
        self.user_log_file =  self.settings["Telegram"]["users_log_file"]

        self.minutes_between_messages = 24 * 60 / (self.posts_per_day + 1)

    def saveSettings(self):
        with open(self.settings_path, 'w') as configfile:
            self.settings.write(configfile)

    def start(self):
        self.updater = Updater(self.token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.jobqueue = self.updater.job_queue

        self.jobqueue.run_repeating(send_memes, interval=59, first=5)
        self.jobqueue.run_once(start_notification, when=0)

        self.dispatcher.add_handler(CommandHandler('start', start))
        self.dispatcher.add_handler(CommandHandler('reset', reset)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('stop', stop)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('nextpost', nextpost, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('addtoqueue', addtoqueue, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('showqueue', showqueue, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('cleanqueue', cleanqueue)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('setsubreddits', setsubreddits, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('viewsubreddits', viewsubreddits)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('viewpostsperday', viewpostsperday)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('setpostsperday', setpostsperday)) #DANGEROUS

        self.dispatcher.add_error_handler(error)
        self.updater.start_polling()

    def idle(self):
        self.updater.idle()

    def updateQueue(self, queue):
        for post in queue:
            self.queue.append(post["url"])
        queue_string = ",".join([x["url"] for x in queue])
        self.settings["Telegram"]["queue"] = str(queue_string)
        self.saveSettings()

    def showStatus(self):
        return {
            "queue" : self.queue,
            "caption" : self.channel_name,
            "channel_name" : self.channel_name,
            "admins" : self.admins,
            "last_posted" : self.last_posted,
            "next_post" : self.next_post,
            "posts_per_day" : self.posts_per_day,
            "posted_today" : self.posted_today,
            "minutes_between_messages" : self.minutes_between_messages,
            "start_time" : self.start_time,
            "time_format" : self.time_format
        }

    def setPostsPerDay(self, posts_per_day):
        self.posts_per_day = posts_per_day
        self.minutes_between_messages = 24 * 60 / (self.posts_per_day + 1)
        self.settings["Telegram"]["posts_per_day"]  = str(self.posts_per_day)
        self.saveSettings()

    def showQueue(self):
        return self.queue

    def popQueue(self):
        self.queue.pop(0)
        if self.queue:
            queue_string = ",".join([x for x in self.queue])
        else:
            queue_string = ""

        self.settings["Telegram"]["queue"] = queue_string
        self.saveSettings()

    def pushQueue(self, url):
        self.queue.insert(0,url)
        queue_string = ",".join([x for x in self.queue])
        self.settings["Telegram"]["queue"] = queue_string
        self.saveSettings()

    def cleanQueue(self):
        self.queue = []
        self.settings["Telegram"]["queue"] = ""
        self.saveSettings();

    def updateLastSent(self, last_posted):
        self.last_posted = last_posted
        timestamp = last_posted.strftime(self.time_format)
        self.settings["Telegram"]["last_posted"] = timestamp
        self.saveSettings()

    def updateNextSend(self, next_post):
        self.next_post = next_post
        timestamp = next_post.strftime(self.time_format)
        self.settings["Telegram"]["next_post"] = timestamp
        self.saveSettings()

    def updatePostedToday(self):
        self.posted_today += 1
        self.settings["Telegram"]["posted_today"] = str(self.posted_today)
        self.saveSettings()

    def setPostedToday(self, posted):
        self.posted_today = posted
        self.settings["Telegram"]["posted_today"] = str(self.posted_today)
        self.saveSettings()

    def recalculateStartTime(self):
        hour = int(self.start_time_string[0])
        minute = int(self.start_time_string[1])
        self.start_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

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
            json.dump(all_user_data, outfile)


def error(update, context):
    """Log Errors caused by Updates."""
    #logging.error(context.error)
    status = t.showStatus()

    for chat_id in status["admins"]:
        message = "*ERROR RAISED*"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    message = f"_Error at time:_ {datetime.now().strftime(status['time_format'])}\n"
    message += f"_Error raised:_ {context.error}\n"
    message += f"_Update:_ {update}"

    for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    logging.error('Update "%s" caused error "%s"', update, context.error)

def start_notification(context: CallbackContext):
    status = t.showStatus()
    message = "*Bot started!*"
    for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def start(update, context):
    chat_id = update.effective_chat.id
    username =  update.effective_chat.username
    name = update.effective_chat.first_name
    status = t.showStatus()

    user_dict = {
        "username" : username,
        "name" : name,
        "chat_id" : chat_id,
        "time" : datetime.now().strftime(status['time_format']),
        "action" : "start"
    }

    t.logUser(user_dict)

    message = "*Welcome in the Memes Only backend bot*\n"
    message += "*This bot is used to manage the Memes Only meme channel*\n"
    message += "_Join us at_ " + status["channel_name"]
    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def reset(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        message = "_Resetting..._"
        t.settings["Telegram"]["last_posted"] = ""
        t.settings["Telegram"]["next_post"] = ""
        t.settings["Telegram"]["queue"] = ""
        t.settings["Telegram"]["posted_today"] = "0"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
        logging.warning("Resetting")
        os.execl(sys.executable, sys.executable, * sys.argv)
    else:
        message = "*This command is for moderators only*"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def stop(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "_Bot stopped_"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
        t.saveSettings()
        t.updater.stop()
        os._exit()
        exit()
    else:
        message = "*This command is for moderators only*"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def nextpost(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        next_post_timestamp = status["next_post"].strftime(status["time_format"])
        message = "_The next meme is scheduled for:_\n"
        message += next_post_timestamp
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def addtoqueue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        if len(context.args) == 0:
            message = "_Pass the new images as args_"
        else:
            for arg in context.args:
                t.pushQueue(arg)
            message = "\n".join(context.args)
            message += "\n_Added to queue_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def showqueue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        message = "_Current message queue:_\n"
        message += "\n".join(status["queue"])
        if not message:
            message = "*The queue is empty*"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def cleanqueue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        t.cleanQueue()
        message = "_The queue has been cleaned_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def setsubreddits(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            message = "_Pass the new subreddits as args_"
        else:
            r.setsubreddits(context.args)
            r.saveSettings()
            message = "_New subreddit list:_\n"
            message += "\n".join(context.args)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def viewsubreddits(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        subreddits = r.showStatus()["subreddits"]
        message = "_Current subreddits:_\n"
        message += "\n".join(subreddits)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def setpostsperday(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            message = "_Pass the number of posts per day as args_"
        else:
            try:
                posts_per_day = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            t.setPostsPerDay(posts_per_day)

            status = t.showStatus()
            now = datetime.now()
            minutes_since_start = int((now - status["start_time"]).seconds / 60)
            memes_today = int(minutes_since_start / status["minutes_between_messages"]) #number of memes that should have been sent today
            next_post = status["start_time"] + timedelta(minutes=(memes_today+1)*status["minutes_between_messages"])
            t.updateNextSend(next_post)

            message = "_Number of posts per day:_ "
            message += str(posts_per_day)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def viewpostsperday(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        posts_per_day = status["posts_per_day"]
        message = "_Posts per day:_ "
        message += str(posts_per_day)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def send_memes(context: CallbackContext):
    logging.info("sending memes routine begin...")
    now = datetime.now()
    status = t.showStatus()

    minutes_since_start = int((now - status["start_time"]).seconds / 60)
    memes_today = int(minutes_since_start / status["minutes_between_messages"]) #number of memes that should have been sent today

    if not status["last_posted"] or not status["next_post"]: #no meme has been sent yet. We need to wait an appropriate time
        logging.info("no meme has been sent today")
        last_posted = status["start_time"] + timedelta(minutes=memes_today*status["minutes_between_messages"])
        next_post = status["start_time"] + timedelta(minutes=(memes_today+1)*status["minutes_between_messages"])
        t.updateLastSent(last_posted)
        t.updateNextSend(next_post)
        status = t.showStatus()

    if (now - status["start_time"]).days > 0:
        #we rolled past midnight.
        #it's time to resest some stuff
        t.setPostedToday(0) #we didn't post anything yet
        t.recalculateStartTime() #reset the start time so we don't fuck it up
        status = t.showStatus()

    if memes_today > status["posted_today"]:
        logging.info("Correcting number of memes sent today")
        #if the bot doesn't start at the correct time, memes are skipped
        #we check if the memes that have been sent today are enough
        #otherwise, it would spam in the first iterations
        t.setPostedToday(memes_today)
        status = t.showStatus()

    if status["posted_today"] >= status["posts_per_day"]: #enough memes have been sent today
        logging.info("too many memes have been already sent today")
        return

    if status["next_post"] < now: #if the date has already passed
        logging.info("time to send a meme...")
        if not status["queue"] or len(status["queue"]) == 0:
            to_post = r.fetch()
            logging.info("Posts loaded")
            t.updateQueue(to_post)

        #it's time to post a meme
        channel_name = status["channel_name"]
        url = status["queue"][0]
        caption = status["caption"]

        context.bot.send_photo(chat_id=channel_name, photo=url, caption=caption)

        last_posted = datetime.now()
        next_post = status["start_time"] + timedelta(minutes=(status["posted_today"]+1)*status["minutes_between_messages"])

        t.updateLastSent(last_posted)
        t.updateNextSend(next_post)
        t.updatePostedToday()

        t.popQueue()
        to_post = r.fetch()
        t.updateQueue(to_post)

    logging.info("...sending memes routine ends")


settings_file = "settings/settings"
logging_path = "logs/bot_log"

logging.basicConfig(filename=logging_path, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', filemode="w+")
#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

r = Reddit(debug=False)
r.loadSettings(settings_file)
r.login()
logging.info("Reddit initialized")

t = Telegram(debug=True)
t.loadSettings(settings_file)
logging.info("Telegram initialized")

t.start()
t.idle()
logging.info("Bot running")
