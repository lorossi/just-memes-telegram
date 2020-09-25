import requests
import json
import datetime
import os
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
            "posted_folder" : self.posted_folder,
            "words_to_skip" : self.words_to_skip,
            "ocr" : self.ocr,
            "hash_threshold" : self.hash_threshold
        }

    def loadSettings(self, filename):
        #loads settings from file
        if not self.debug:
            self.settings_path = filename
        else:
            #if we are in debug mode, we load all the settings from the
            #appropriate file
            self.settings_path = filename + "_debug"

        with open(self.settings_path) as json_file:
            self.settings = json.load(json_file)["Reddit"]

        #reddit app id
        self.id = self.settings["id"]
        #reddit app token
        self.token = self.settings["token"]
        #number of posts to fetch each time
        self.request_limit = int(self.settings["request_limit"])
        #list of subreddits to fetch posts from, comma separated
        self.subreddits = self.settings["subreddits"]
        #folder and path of the files that contains the list of already posted memes
        self.posted_folder = self.settings["posted_folder"]
        self.posted_full_path = self.posted_folder + "/" + "posted"
        #number of days before old memes are cleaned from the posted list
        self.max_days = int(self.settings["max_days"])
        #path of the tesseract file
        self.tesseract_path = self.settings["tesseract_path"]
        pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        #list of words that we don't want to find on a meme posted on Telegram,
        #comma separated
        self.words_to_skip = self.settings["words_to_skip"]
        self.hash_threshold = self.settings["hash_threshold"]
        self.ocr = self.settings["ocr"]

    def login(self):
        #Login to Reddit app api
        self.reddit = praw.Reddit(client_id=self.id,
                     client_secret=self.token,
                     user_agent='PC')

    def saveSettings(self):
        #Save all object settings to file
        with open(self.settings_path) as json_file:
            old_settings = json.load(json_file)

        old_settings["Reddit"].update(self.settings)

        with open(self.settings_path, 'w') as outfile:
            json.dump(old_settings, outfile, indent=4)

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

                if submission.title:
                    title = submission.title
                else:
                    title = None

                #Append the current found post to the list of posts
                self.posts.append(
                    {
                        "id" : submission.id,
                        "url" : submission.url,
                        "subreddit" : subreddit,
                        "title" : title,
                        "caption" : None,
                        "hash" : None,
                        "timestamp" : timestamp
                    }
                )

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
            hash = self.hash_threshold > 0
            fingerprint = image_fingerprint(self.posts[0]["url"], ocr=self.ocr, hash=hash)

            self.posts[0]["hash"] = fingerprint["string_hash"]
            self.posts[0]["caption"] = fingerprint["caption"]
            self.to_post = self.posts[0]
            return True

        for post in self.posts: #current loaded posts
            found = False
            if any (word.lower() in post["title"].lower() for word in self.words_to_skip):
                #we found one of the words to skip
                logging.warning("REPOST: title of %s contains banned word(s). Title: %s", post["title"])
                continue

            hash = self.hash_threshold > 0
            fingerprint = image_fingerprint(post["url"], hash=hash, ocr=self.ocr)
            if not fingerprint:
                logging.error("Couldn't fingerprint image %s", post["url"])
                continue

            for posted in self.posted: #posted posts

                if not posted:
                    continue

                posted_hash = imagehash.hex_to_hash(posted["hash"]) #old hash

                if post["id"] == posted["id"]: #this meme has already been posted...
                    logging.info("Post id %s has already been posted", posted["id"])
                    found = True
                    break

                if fingerprint["caption"] == posted["caption"]:
                    logging.info("Post with caption %s has already been posted", posted["caption"])
                    found = True
                    break

                if not fingerprint["hash"]:
                    logging.info("skipping hash for %s", post["id"])
                elif fingerprint["hash"] - posted_hash < self.hash_threshold: #if the images are too similar
                    found = True
                    #it's a repost
                    logging.warning("REPOST: %s is too similar to %s. similarity: %s", post["id"], posted["id"], str(fingerprint["hash"]-posted_hash))
                    break #don't post it

                if fingerprint["caption"] and any (word.lower() in fingerprint["caption"].lower().replace("\\n", " ") for word in self.words_to_skip):
                    found = True
                    #we found one of the words to skip
                    logging.warning("REPOST: %s contains banned word(s). Complete caption: %s", post["url"], fingerprint["caption"].lower().replace("\\n", " "))
                    break

            if not found:
                post["hash"] = fingerprint["string_hash"]
                post["caption"] = fingerprint["caption"]
                self.to_post = post
                return True

        return False #no new posts...

    def updatePosted(self, more_post=None):
        #Update posted file

        if not self.to_post and not more_post:
            return

        logging.info("Saving posted list...")
        with open(self.posted_full_path) as json_file:
            posted_data = json.load(json_file)

        if more_post:
            posted_data.append(more_post)
        else:
            posted_data.append(self.to_post)

        with open(self.posted_full_path, 'w') as json_file:
            json.dump(posted_data, json_file, indent=4)

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
        #self.updatePosted()
        logging.info("Memes fetched")
        return self.to_post

    def setsubreddits(self, subreddits):
        #Sets the new list of subreddits and saves it to file
        self.subreddits = []
        for subreddit in subreddits:
            self.subreddits.append(subreddit)

        self.settings["subreddits"] = self.subreddits


    def setwordstoskip(self, wordstoskip):
        self.wordstoskip = []
        for word in wordstoskip:
            self.wordstoskip.append(word)

        self.settings["words_to_skip"] = self.wordstoskip

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

    def toggleOcr(self):
        self.ocr = not self.ocr
        self.settings["ocr"] = not self.settings["ocr"]

    def setThreshold(self, threshold):
        self.hash_threshold = threshold
        self.settings["hash_threshold"] = threshold


class Telegram:
    #Class that handles all the Telegram stuff
    def __init__(self, debug=False):
        self.debug = debug #Debug mode: secondary channel and bot
        self.time_format = '%Y/%m/%d %H:%M:%S'
        self.version = "1.5.2" #current bot version
        self.calculateStartTime() #calculates the time for the first post

    def loadSettings(self, filename):
        if not self.debug:
            self.settings_path = filename
        else:
            self.settings_path = filename + "_debug"

        with open(self.settings_path) as json_file:
            self.settings = json.load(json_file)["Telegram"]

        self.token = self.settings["token"]
        self.channel_name = self.settings["channel_name"]
        self.posts_per_day = self.settings["posts_per_day"]

        last_posted_timestamp = self.settings["last_posted"]
        if last_posted_timestamp:
            last_posted_struct = time.strptime(last_posted_timestamp, self.time_format)
            self.last_posted = datetime.datetime(*last_posted_struct[:6])
        else:
            self.last_posted = None

        self.next_post = None

        self.queue = self.settings["queue"]


        self.admins = self.settings["admins"]
        self.user_log_file =  self.settings["users_log_file"]
        self.minutes_between_messages = 24 * 60 / self.posts_per_day

    def saveSettings(self):
        with open(self.settings_path) as json_file:
            old_settings = json.load(json_file)

        old_settings["Telegram"].update(self.settings)

        with open(self.settings_path, 'w') as outfile:
            json.dump(old_settings, outfile, indent=4)

    def start(self):
        self.updater = Updater(self.token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.jobqueue = self.updater.job_queue

        self.jobqueue.run_repeating(send_memes, interval=15, first=0, name="send_memes")
        self.jobqueue.run_once(start_notification, when=1, name="start_notification")

        self.jobqueue.run_daily(new_day, datetime.time(00, 5, 00, 000000), name="new_day")

        self.dispatcher.add_handler(CommandHandler('start', start))
        self.dispatcher.add_handler(CommandHandler('reset', reset)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('stop', stop)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('botstatus', botstatus)) #DANGEROUS

        self.dispatcher.add_handler(CommandHandler('nextpost', nextpost, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('queue', queue, pass_args=True))

        self.dispatcher.add_handler(CommandHandler('subreddits', subreddits, pass_args=True))
        self.dispatcher.add_handler(CommandHandler('postsperday', postsperday, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('wordstoskip', wordstoskip, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('toggleocr', toggleocr, pass_args=True)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('imagethreshold', imagethreshold, pass_args=True)) #DANGEROUS

        self.dispatcher.add_handler(CommandHandler('cleanqueue', cleanqueue)) #DANGEROUS
        self.dispatcher.add_handler(CommandHandler('cleanpostedlist', cleanpostedlist)) #DANGEROUS

        self.dispatcher.add_error_handler(error)
        self.updater.start_polling()

    def idle(self):
        self.updater.idle()

    def updateQueue(self, post):
        now = datetime.datetime.now() # current date and time
        timestamp = now.strftime("%Y%m%d")
        self.queue.append(post)
        self.settings["queue"] = self.queue
        #self.saveSettings()

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
        self.next_post = None
        self.settings["posts_per_day"]  = self.posts_per_day
        #self.saveSettings()

    def popQueue(self):
        self.queue.pop(0)
        #queue_string = json.dumps(self.queue)
        self.settings["queue"] = self.queue
        #self.saveSettings()

    def cleanQueue(self):
        self.queue = []
        self.settings["queue"] = []
        #self.saveSettings();

    def updateLastSent(self, last_posted):
        if last_posted:
            timestamp = last_posted.strftime(self.time_format)
        else:
            timestamp = None

        self.last_posted = last_posted

        self.settings["last_posted"] = timestamp
        #self.saveSettings()

    def updateNextPost(self, next_post):
        if next_post:
            timestamp = next_post.strftime(self.time_format)
        else:
            timestamp = None

        self.next_post = next_post

        self.settings["next_post"] = timestamp
        #self.saveSettings()

    def calculateStartTime(self):
        #Midnight
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
            json.dump(all_user_data, outfile, indent=4)

def error(update, context):
    """Log Errors caused by Updates."""
    #logging.error(context.error)
    status = t.showStatus()

    for chat_id in status["admins"]:
        message = "*ERROR RAISED*"
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    error_string = str(context.error).replace("_", "\\_") #MARKDOWN escape
    message = f"Error at time: {datetime.datetime.now().strftime(status['time_format'])}\n"
    message = f"Error raised: {error_string}\n"
    message += f"Update: {update}"

    for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message)

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

    message = "*Welcome in the Just Memes backend bot*\n"
    message += "*This bot is used to manage the Just Memes meme channel*\n"
    message += "_Join us at_ " + status["channel_name"]
    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def reset(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        message = "_Resetting..._"
        t.updateLastSent(None)
        t.updateNextPost(None)
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

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def nextpost(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()

    if chat_id in status["admins"]:
        if (status["next_post"]):
            next_post_timestamp = status["next_post"].strftime(status["time_format"])
            message = "_The next meme is scheduled for:_\n"
            message += next_post_timestamp
        else:
            message = "_The bot hasn't completed it startup process yet. Wait a few seconds!_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def toggleocr(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        status = r.showStatus()
        r.toggleOcr()
        r.saveSettings()
        message = "_Ocr is now:_ "
        message += "Disabled" if status["ocr"] else "Enabled"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def imagethreshold(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        status = r.showStatus()
        if len(context.args) == 0:
            image_treshold = status["hash_threshold"]
            message = "_Current hash theshold:_ "
            message += str(image_treshold)
            message += "\n_Pass the threshold as arguments to set a new value_"
        else:
            try:
                image_treshold = int(context.args[0])
            except:
                message = "_The arg provided is not a number_"
                context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
                return

            r.setThreshold(image_treshold)
            r.saveSettings()

            status = r.showStatus()

            message = "_Image threshold:_ "
            message += str(image_treshold)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def cleanqueue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        t.cleanQueue()
        t.saveSettings()
        message = "_The queue has been cleaned_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def queue(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            if len(status["queue"]) > 0:
                message = "_Current message queue:_\n"
                message += json.dumps(status["queue"], indent=4, sort_keys=True)
            else:
                message = "*The queue is empty*"
                message += "\n_Pass the links as arguments to set them_"
        else:
            now = datetime.datetime.now() # current date and time
            timestamp = now.strftime("%Y%m%d")

            for arg in context.args:
                fingerprint = image_fingerprint(arg)

                to_post = {
                    "id" : None,
                    "url" : str(arg),
                    "subreddit" : None,
                    "title" : None,
                    "caption" : fingerprint["caption"],
                    "hash" : fingerprint["string_hash"],
                    "timestamp" : timestamp
                }

                t.updateQueue(to_post)
                t.saveSettings()
                r.updatePosted(to_post)

            message = "_Image(s) added to queue_\n"
            message += "_Use /queue to chech the current queue_"
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

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
            r.setsubreddits(context.args)
            r.saveSettings()

            message = "_New subreddit list:_\n•"
            message += "\n•".join(context.args).replace("_", "\\_")
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
            t.updateNextPost(next_post)
            t.saveSettings()

            message = "_Number of posts per day:_ "
            message += str(posts_per_day)
    else:
        message = "*This command is for moderators only*"

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

def wordstoskip(update, context):
    chat_id = update.effective_chat.id
    status = t.showStatus()
    if chat_id in status["admins"]:
        if len(context.args) == 0:
            words_to_skip = r.showStatus()["words_to_skip"]
            message = "_Current words to be skpped:_\n•"
            message += "\n•".join(words_to_skip).replace("_", "\\_")
            message += "\n_Pass the words to skip as arguments to set them_"
        else:
            r.setwordstoskip(context.args)
            r.saveSettings()
            message = "_New list of words to skip:_\n•"
            message += "\n•".join(context.args).replace("_", "\\_")
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

    if not status["last_posted"] or not status["next_post"]: #no meme has been sent yet OR time has been changed. We need to wait an appropriate time
        logging.info("no meme has been sent today")
        last_posted = status["start_time"] + datetime.timedelta(minutes=memes_today*status["minutes_between_messages"])
        next_post = status["start_time"] + datetime.timedelta(minutes=(memes_today+1)*status["minutes_between_messages"])
        t.updateLastSent(last_posted)
        t.updateNextPost(next_post)
        t.saveSettings()
        status = t.showStatus()


    if status["next_post"] <= now: #if the date has already passed
        logging.info("time to send a meme...")
        last_posted = status["next_post"]

        next_post = last_posted + datetime.timedelta(minutes=(status["minutes_between_messages"]))
        next_post = next_post.replace(second=0, microsecond=0)

        if len(status["queue"]) == 0:
            to_post = r.fetch()
            logging.info("Posts loaded")
            #adds the photo to bot queue so we can use this later
            t.updateQueue(to_post)
            t.saveSettings()
        else:
            logging.info("Loading meme from queue")

        status = t.showStatus()
        #it's time to post a meme
        channel_name = status["channel_name"]
        url = status["queue"][0]["url"]
        caption = status["caption"]
        logging.info("Sending image with url %s", url)
        context.bot.send_photo(chat_id=channel_name, photo=url, caption=caption)
        t.saveSettings()
        t.updateLastSent(last_posted)
        t.updateNextPost(next_post)
        r.updatePosted()
        # we remove rhe last posted link from queue
        t.popQueue()
    logging.info("...sending memes routine ends")

def new_day(context: CallbackContext):
    logging.info("New day routine...")
    status = t.showStatus()
    message = "*New day routine!*"

    """for chat_id in status["admins"]:
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)"""

    r.cleanPosted()
    logging.info("...new day routine ended")

def image_fingerprint(url, hash=True, ocr=True):
    try:
        r = requests.get(url, stream=True)
        r.raw.decode_content = True # handle spurious Content-Encoding
        #Open it in PIL
        im = Image.open(r.raw)
        #Hash it
        if hash:
            hash = imagehash.average_hash(im)
        else:
            ocr = None
        #OCR it
        if ocr:
            caption = pytesseract.image_to_string(im).lower()
        else:
            caption = None
        #close the image
        im.close()
    except Exception as e:
        logging.error("ERROR while fingerprinting %s %s", e, url)
        return None

    return {
        "hash" : hash,
        "string_hash" : str(hash),
        "caption" : caption
    }


settings_file = "settings/settings.json"
logging_path = "logs/memes_only_telegram.log"

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
logging.info("Bot running")
t.idle()
