import requests
import json
import datetime
import os
import configparser
import logging
import time
import sys
import praw
import imagehash
import pytesseract
from PIL import Image
from telegram import ParseMode
from telegram.ext import Updater, CommandHandler, JobQueue, CallbackContext

class Reddit:
    #This class handles all the connections to Reddit, including:
    #---Post Fetching
    #---Image OCR to prevent Reddit-only memes to be posted on Telegram
    #---Image Hashing to prevent reposts to be posted on Telegram

    def __init__(self, debug=False):
        self.debug = debug #debug mode: offline post list (file request)
        self.posts = [] #list of fetched posts
        self.to_post = [] #list of post to be posted
        self.posted = [] #list of post that have been already posted

    def showStatus(self):
        #Returns some stats on the class
        return {
            "request_limit" : self.request_limit,
            "subreddits" : self.subreddits,
            "posted_folder" : self.posted_folder
        }

    def loadSettings(self, filename):
        #loads settings from file
        if not self.debug:
            self.settings_path = filename
        else:
            #if we are in debug mode, we load all the settings from the
            #appropriate file
            self.settings_path = filename + "_debug"

        self.settings = configparser.ConfigParser()
        self.settings.read(self.settings_path)

        #reddit app id
        self.id = self.settings["Reddit"]["id"]
        #reddit app token
        self.token = self.settings["Reddit"]["token"]
        #number of posts to fetch each time
        self.request_limit = int(self.settings["Reddit"]["request_limit"])
        #list of subreddits to fetch posts from, comma separated
        self.subreddits = self.settings["Reddit"]["subreddits"].split(",")
        #folder and path of the files that contains the list of already posted memes
        self.posted_folder = self.settings["Reddit"]["posted_folder"]
        self.posted_full_path = self.posted_folder + "/" + "posted"
        #number of days before old memes are cleaned from the posted list
        self.max_days = int(self.settings["Reddit"]["max_days"])
        #path of the tesseract file
        self.tesseract_path = self.settings["Reddit"]["tesseract_path"]
        pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        #list of words that we don't want to find on a meme posted on Telegram,
        #comma separated
        self.words_to_skip = self.settings["Reddit"]["words_to_skip"].split(",")

    def login(self):
        #Login to Reddit app api
        self.reddit = praw.Reddit(client_id=self.id,
                     client_secret=self.token,
                     user_agent='PC')

    def saveSettings(self):
        #Save all object settings to file
        with open(self.settings_path, 'w') as configfile:
            self.settings.write(configfile)

    def loadPosts(self):
        #Loads new posts
        now = datetime.datetime.now() # current date and time
        timestamp = now.strftime("%Y%m%d")

        self.posts = []

        for submission in self.reddit.subreddit("+".join(self.subreddits)).hot(limit=self.request_limit):

            if not submission:
                #we found nothing
                return False
            #we want to skip selftexts and stickied submission
            if not submission.selftext and not submission.stickied:

                #we want to skip gifs
                if "v.redd.it" in submission.url:
                    continue
                if ".gif" in submission.url:
                    continue

                #fail-proof way of discovering the post's subreddit
                if submission.subreddit_name_prefixed:
                    subreddit = submission.subreddit_name_prefixed
                else:
                    subreddit = None

                try:
                    #Checksum creation
                    #Load the image by url (so we don't have to download it)
                    r = requests.get(submission.url, stream=True)
                    r.raw.decode_content = True # handle spurious Content-Encoding
                    #Open it in PIL
                    im = Image.open(r.raw)
                    #Hash it
                    hash = str(imagehash.average_hash(im))
                    #OCR it
                    caption = pytesseract.image_to_string(im).lower()

                    if any (word in self.words_to_skip for word in caption):
                        #we found one of the strings to skip
                        continue

                    im.close()

                    #Append the current found post to the list of posts
                    self.posts.append(
                        {
                            "id" : submission.id,
                            "url" : submission.url,
                            "subreddit" : subreddit,
                            "caption" : caption,
                            "hash" : hash,
                            "timestamp" : timestamp
                        }
                    )
                except Exception as e:
                    logging.error("error while hashing: %s %s", e, submission.url)
                    pass

        #we found at leat a post
        return True

    def findNew(self):
        #Checks if new posts loaded (currently in self.posts list) are in fact
        #new or just a repost.

        #Load posted file
        if os.path.exists(self.posted_full_path):
            with open(self.posted_full_path) as json_file:
                self.posted = json.load(json_file)
        else:
            open(self.posted_full_path, "w+")
            self.posted = []

        self.to_post = []

        #If no meme has been posted yet, there's no need tho check
        if len(self.posted) == 0:
            logging.info("No meme has been posted before!")
            self.to_post = self.posts[0]
            return True

        for post in self.posts: #current loaded posts
            found = False

            for posted in self.posted: #posted posts
                if post["id"] == posted["id"]: #this meme has already been posted...
                    found = True
                    break

                posted_hash = imagehash.hex_to_hash(posted["hash"]) #old hash
                post_hash = imagehash.hex_to_hash(post["hash"]) #new hash

                if post_hash - posted_hash < 10: #if the images are too similar
                    #it's a repost
                    logging.warning("REPOST: %s is too similar to %s. similarity: %s", post["id"], posted["id"], str(post_hash-posted_hash))
                    found = True
                    break #don't post it

            if not found:
                self.to_post = post
                return True

        return False #no new posts...

    def updateList(self):
        #Update posted file

        logging.info("Saving posted list...")
        with open(self.posted_full_path) as json_file:
            posted_data = json.load(json_file)

        posted_data.append(self.to_post)

        with open(self.posted_full_path, 'w') as json_file:
            json.dump(posted_data, json_file)
        logging.info("Saved posted list!")

    def fetch(self):
        #Funciton used to join the routine of loading posts and checking if
        #they are in fact new
        logging.info("Fetching new memes...")

        while not self.loadPosts():
            #Something went wrong, we cannot load new posts. Maybe reddit is
            #down?
            logging.info("Cannot load posts... trying again in 10")
            time.sleep(10)

        logging.info("%s posts found!", str(len(self.posts)))

        while not self.findNew():
            #We didn't find any new post... let's wait a while and try again
            logging.info("Cannot find new posts... trying again in 10")
            time.sleep(10)
            self.loadPosts()

        #update list of posted memes
        self.updateList()
        logging.info("Memes fetched")
        return self.to_post

    def setsubreddits(self, subreddits):
        #Sets the new list of subreddits and saves it to file
        self.subreddits = []
        for subreddit in subreddits:
            self.subreddits.append(subreddit)
        self.settings["Reddit"]["subreddits"] = ",".join(self.subreddits)
        self.saveSettings()

    def cleanPosted(self):
        #Deletes the list of already posted subreddits
        logging.info("Cleaning posted data...")
        now = datetime.datetime.now() # current date and time
        timestamp = now.strftime("%Y%m%d")

        with open(self.posted_full_path) as json_file:
            posted_data = json.load(json_file)

        for posted in posted_data:
            #Loop through all the posted memes and check the time at which they
            #were posted
            posted_time = datetime.datetime.strptime(posted["timestamp"], "%Y%m%d")
            if (now - posted_time).days > self.max_days:
                #if the post has been posted too long ago, it has no sense to
                #still check if it has been posted already. Let's just mark it
                #so it can be later deleted
                posted["delete"] = True

        #Let's keep all the post that are not flagged and save it to file
        posted_data = [x for x in posted_data if not "delete" in x]
        with open(self.posted_full_path, 'w') as outfile:
            json.dump(posted_data, outfile)

class Telegram:
    #Class that handles all the Telegram stuff
    def __init__(self, debug=False):
        self.debug = debug #Debug mode: secondary channel and bot
        self.time_format = '%Y/%m/%d %H:%M:%S'
        self.version = "1.2.1" #current bot version
        self.calculateStartTime() #calculates the time for the first post

    def loadSettings(self, filename):
        if not self.debug:
            self.settings_path = filename
        else:
            self.settings_path = filename + "_debug"

        self.settings = configparser.ConfigParser()
        self.settings.read(self.settings_path)

        self.token = self.settings["Telegram"]["token"]
        self.channel_name = self.settings["Telegram"]["channel_name"]
        self.posts_per_day = int(self.settings["Telegram"]["posts_per_day"])

        last_posted_timestamp = self.settings["Telegram"]["last_posted"]
        if last_posted_timestamp:
            last_posted_struct = time.strptime(last_posted_timestamp, self.time_format)
            self.last_posted = datetime.datetime(*last_posted_struct[:6])
        else:
            self.last_posted = None

        next_post_timestamp = self.settings["Telegram"]["next_post"]
        if next_post_timestamp:
            next_post_struct = time.strptime(next_post_timestamp, self.time_format)
            self.next_post = datetime.datetime(*next_post_struct[:6])
        else:
            self.next_post = None

        if self.settings["Telegram"]["queue"] != "":
            queue_string = self.settings["Telegram"]["queue"]
            self.queue = json.loads(queue_string)
        else:
            self.queue = []


        self.admins = [int(x) for x in self.settings["Telegram"]["admins"].split(",") if x]
        self.user_log_file =  self.settings["Telegram"]["users_log_file"]
        self.minutes_between_messages = 24 * 60 / self.posts_per_day

    def saveSettings(self):
        with open(self.settings_path, 'w') as configfile:
            self.settings.write(configfile)

    def start(self):
        self.updater = Updater(self.token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.jobqueue = self.updater.job_queue

        self.jobqueue.run_repeating(send_memes, interval=30, first=5, name="send_memes")
        self.jobqueue.run_once(start_notification, when=0, name="start_notification")

        self.jobqueue.run_daily(new_day, datetime.time(00, 5, 00, 000000), name="new_day")

        self.dispatcher.add_handler(CommandHandler('start', start))
        self.dispatcher.add_handler(CommandHandler('reset', reset)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('stop', stop)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('botstatus', botstatus)) #DANGEROUS

        self.dispatcher.add_handler(CommandHandler('nextpost', nextpost, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('addtoqueue', addtoqueue, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('viewqueue', viewqueue, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('cleanqueue', cleanqueue)) #DANGEROUS

        self.dispatcher.add_handler(CommandHandler('subreddits', subreddits, pass_args=True))
        self.dispatcher.add_handler(CommandHandler('postsperday', postsperday, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('viewcurrentversion', viewcurrentversion)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('cleanpostedlist', cleanpostedlist)) #DANGEROUS

        self.dispatcher.add_error_handler(error)
        self.updater.start_polling()

    def idle(self):
        self.updater.idle()

    def updateQueue(self, post):
        now = datetime.datetime.now() # current date and time
        timestamp = now.strftime("%Y%m%d")
        self.queue.append(post)
        queue_string = json.dumps(self.queue)
        self.settings["Telegram"]["queue"] = queue_string
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
            "minutes_between_messages" : self.minutes_between_messages,
            "start_time" : self.start_time,
            "time_format" : self.time_format,
            "version" : self.version
        }

    def setPostsPerDay(self, posts_per_day):
        self.posts_per_day = posts_per_day
        self.minutes_between_messages = 24 * 60 / self.posts_per_day
        self.settings["Telegram"]["posts_per_day"]  = str(self.posts_per_day)
        self.saveSettings()

    def popQueue(self):
        self.queue.pop(0)
        if self.queue:
            queue_string = json.dumps(self.queue)
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
        if last_posted:
            timestamp = last_posted.strftime(self.time_format)
        else:
            timestamp = ""

        self.last_posted = last_posted

        self.settings["Telegram"]["last_posted"] = timestamp
        self.saveSettings()

    def updateNextSend(self, next_post):
        if next_post:
            timestamp = next_post.strftime(self.time_format)
        else:
            timestamp = ""

        self.next_post = next_post

        self.settings["Telegram"]["next_post"] = timestamp
        self.saveSettings()

    def calculateStartTime(self):
        self.start_time = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

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

    error_string = str(context.error).replace("_", "\\_") #MARKDOWN escape
    message = f"_Error at time:_ {datetime.datetime.now().strftime(status['time_format'])}\n"
    message += f"_Error raised:_ {error_string}\n"
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
        "time" : datetime.datetime.now().strftime(status['time_format']),
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
        #t.settings["Telegram"]["posted_today"] = "0"
        t.saveSettings()

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

def botstatus(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "_Bot status:_\n"
        for key, value in status.items():
            message += "*" + str(key) + ":* "
            message += "_" + str(value).replace("_", "_") + "_\n"
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
            message = "_Pass the new image(s) url(s) as args_"
        else:
            now = datetime.datetime.now() # current date and time
            timestamp = now.strftime("%Y%m%d")

            for arg in context.args:
                r = requests.get(arg, stream=True)
                r.raw.decode_content = True # handle spurious Content-Encoding
                im = Image.open(r.raw)
                hash = str(imagehash.average_hash(im))
                im.close()

                t.pushQueue({
                    "id" : None,
                    "url" : arg,
                    "subreddit" : None,
                    "caption" : None,
                    "hash" : hash,
                    "timestamp" : timestamp
                })

            message = "\n".join(context.args)
            message += "\n_Added to queue_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def viewqueue(update, context):
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

def subreddits(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            subreddits = r.showStatus()["subreddits"]
            message = "_Current subreddits:_\n"
            message += "\n".join(subreddits).replace("_", "\\_")
            message += "\n_Pass the number of posts per day as arguments to set a new value_"
        else:
            r.setsubreddits(context.args)
            r.saveSettings()
            message = "_New subreddit list:_\n"
            message += "\n".join(context.args).replace("_", "\\_")
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def postsperday(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            posts_per_day = status["posts_per_day"]
            message = "_Posts per day:_ "
            message += str(posts_per_day)
            message += "\n_Pass the number of posts per day as arguments to set a new value_"
        else:
            try:
                posts_per_day = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            t.setPostsPerDay(posts_per_day)

            status = t.showStatus()
            now = datetime.datetime.now()
            minutes_since_start = int((now - status["start_time"]).seconds / 60)
            memes_today = int(minutes_since_start / status["minutes_between_messages"]) #number of memes that should have been sent today
            last_posted = status["start_time"] + datetime.timedelta(minutes=memes_today*status["minutes_between_messages"])
            next_post = status["start_time"] + datetime.timedelta(minutes=(memes_today+1)*status["minutes_between_messages"])

            t.updateLastSent(last_posted)
            t.updateNextSend(next_post)

            message = "_Number of posts per day:_ "
            message += str(posts_per_day)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def viewcurrentversion(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        version = status["version"]
        message = "_Version:_ "
        message += version
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def cleanpostedlist(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        empty_data = []
        with open(r.posted_full_path, 'w') as outfile:
            json.dump(empty_data, outfile)

        message = "*Posted list cleaned*"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def send_memes(context: CallbackContext):
    logging.info("sending memes routine begin...")
    now = datetime.datetime.now()
    status = t.showStatus()

    minutes_since_start = int((now - status["start_time"]).seconds / 60)
    memes_today = int(minutes_since_start / status["minutes_between_messages"]) #number of memes that should have been sent today

    if not status["last_posted"] or not status["next_post"]: #no meme has been sent yet. We need to wait an appropriate time
        logging.info("no meme has been sent today")
        last_posted = status["start_time"] + datetime.timedelta(minutes=memes_today*status["minutes_between_messages"])
        next_post = status["start_time"] + datetime.timedelta(minutes=(memes_today+1)*status["minutes_between_messages"])
        t.updateLastSent(last_posted)
        t.updateNextSend(next_post)
        status = t.showStatus()


    if status["next_post"] <= now: #if the date has already passed
        logging.info("time to send a meme...")
        last_posted = status["next_post"]
        next_post = last_posted + datetime.timedelta(minutes=(status["minutes_between_messages"]))

        if not status["queue"] or len(status["queue"]) == 0:
            to_post = r.fetch()
            logging.info("Posts loaded")
            t.updateQueue(to_post)
            status = t.showStatus()

        #it's time to post a meme
        channel_name = status["channel_name"]
        url = status["queue"][0]["url"]
        caption = status["caption"]

        context.bot.send_photo(chat_id=channel_name, photo=url, caption=caption)

        t.popQueue()
        t.updateLastSent(last_posted)
        t.updateNextSend(next_post)

    logging.info("...sending memes routine ends")

def new_day(context: CallbackContext):
    logging.info("New day routine...")
    status = t.showStatus()
    message = "*New day routine!*"
    for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    r.cleanPosted()
    logging.info("...new day routine ended")


settings_file = "settings/settings"
logging_path = "logs/bot_log"

logging.basicConfig(filename=logging_path, level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s', filemode="w+")
#logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

r = Reddit(debug=False)
r.loadSettings(settings_file)
r.login()
logging.info("Reddit initialized")

t = Telegram(debug=False)
t.loadSettings(settings_file)
logging.info("Telegram initialized")

t.start()
t.idle()
logging.info("Bot running")
