import praw
import ujson
import string
import logging
import requests
import imagehash
import pytesseract

from PIL import Image
from time import sleep
from datetime import datetime


class Reddit:
    # This class handles all the connections to Reddit, including:
    # ---Post Fetching
    # ---Image OCR to prevent Reddit-only memes to be posted on Telegram
    # ---Image Hashing to prevent reposts to be posted on Telegram

    def __init__(self):
        self._posts = []  # list of fetched posts
        self._post_queue = []  # list of next post
        self._posted = []  # list of post that have been already posted
        self._to_discard = []  # list of posts to discard
        self._settings = {}
        self._settings_path = "settings/settings.json"
        self._loadSettings()
        self._login()

    def _loadSettings(self):
        # loads settings from file
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Reddit"]
        logging.info("Settings loaded")

    def _saveSettings(self):
        # Save all object settings to file
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        old_settings["Reddit"].update(self._settings)

        print(ujson.dumps(old_settings, indent=2))

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _loadPosted(self):
        # Load posted file
        try:
            with open(self._settings["posted_file"]) as json_file:
                self._posted = ujson.load(json_file)
            logging.info("Loaded posted file")
        except FileNotFoundError:
            logging.info("Posted file not found. Creating it.")
            self._posted = []

    def _loadDiscarded(self):
        # Load discarded file
        try:
            with open(self._settings["discarded_file"]) as json_file:
                self._discarded = ujson.load(json_file)
            logging.info("Loaded discarded file")
        except FileNotFoundError:
            logging.info("Discarded file not found. Creating it.")
            self._discarded = []

    def _login(self):
        # Login to Reddit app api
        self.reddit = praw.Reddit(client_id=self._settings["id"],
                                  client_secret=self._settings["token"],
                                  user_agent='PC')

    def _imageFingerprint(self, url, hash=True, ocr=True):
        try:
            r = requests.get(url, stream=True)
            # handle spurious Content-Encoding
            r.raw.decode_content = True
            # Open it in PIL
            im = Image.open(r.raw)
            # Hash it
            hash = imagehash.average_hash(im) if hash else None
            # OCR it
            if ocr:
                raw_caption = pytesseract.image_to_string(im)
                raw_caption = raw_caption.lower().strip()

                if raw_caption == "":
                    caption = ""
                else:
                    printable = set(string.printable)
                    caption = "".join(
                        filter(lambda x: x in printable, raw_caption)
                    )
            else:
                caption = None

            # close the image
            im.close()

        except Exception as e:
            logging.error(f"ERROR while fingerprinting {e} {url}")
            return None

        return {
            "hash": hash,
            "string_hash": str(hash),
            "caption": caption
        }

    def _loadPosts(self):
        logging.info("Loading new posts...")
        # Loads new posts
        now = datetime.now()  # current date and time
        timestamp = now.isoformat()

        self._posts = []
        subreddit_list = "+".join(self._settings["subreddits"])

        for submission in self.reddit.subreddit(subreddit_list) \
                .hot(limit=self._settings["request_limit"]):

            if not submission:
                # we found nothing
                return 0

            # we want to skip selftexts and stickied submission
            if submission.selftext or submission.stickied:
                continue

            # we want to skip gifs
            if "v.redd.it" in submission.url:
                continue
            if ".gif" in submission.url:
                continue

            # fail-proof way of discovering the post's subreddit
            if submission.subreddit_name_prefixed:
                subreddit = submission.subreddit_name_prefixed
            else:
                subreddit = None

            title = submission.title or None

            # Append the current found post to the list of posts
            self._posts.append(
                {
                    "id": submission.id,
                    "url": submission.url,
                    "subreddit": subreddit,
                    "title": title,
                    "caption": None,
                    "hash": None,
                    "timestamp": timestamp
                }
            )

        logging.info(f"{len(self._posts)} posts loaded")
        # we found at leat a post
        return len(self._posts)

    def _isAlreadyDiscarded(self, post):
        for discarded in self._discarded:
            if "id" in post and "id" in discarded:
                if post["id"] == discarded["id"]:
                    logging.info(
                        f"Post id {discarded['id']} has "
                        "already been discarded"
                    )
                    return True

            if post["url"] == discarded["url"]:
                logging.info(
                    f"url { post['url']} has already been discarded"
                )
                return True

    def _containsSkipWords(self, post):
        lower_title = post["title"].lower()
        # check if the title contains one of the words to skip
        if any(word in lower_title for word in
               self._settings["words_to_skip"]):
            # we found one of the words to skip
            logging.warning(
                f"REPOST: title contains banned word(s). "
                f"Title: {post['title']}"
            )
            return True

        return False

    def _isAlreadyPosted(self, post):  # sourcery skip: merge-nested-ifs
        fingerprint = self._imageFingerprint(
            post["url"],
            hash=self._settings["hash_threshold"] > 0,
            ocr=self._settings["ocr"],
        )

        if not fingerprint:
            logging.error(f"Couldn't fingerprint image {post['url']}")
            return

        for posted in self._posted:
            posted_hash = imagehash.hex_to_hash(posted["hash"])  # old hash
            # this meme has already been posted...
            if "id" in post and "id" in posted:
                if post["id"] == posted["id"]:
                    logging.info(
                        f"Post id {posted['id']} has already been posted"
                    )
                    return True

            if post["url"] == posted["url"]:
                logging.info(
                    f"url {posted['url']} has already been posted"
                )
                return True

            if "caption" in posted and "caption" in fingerprint:
                if fingerprint["caption"] == posted["caption"]:
                    logging.info(
                        f"Post with caption {posted['caption']} "
                        "has already been posted"
                    )
                    return True

            if not fingerprint["hash"]:
                logging.info(f"Skipping hash for {post['id']}")
            else:
                difference = fingerprint["hash"] - posted_hash
            # if the images are too similar
                if difference < self._settings["hash_threshold"]:
                    self._to_discard.append(post)
                    # it's a repost
                    logging.warning(
                        f"REPOST: {post['id']} is too similar to "
                        f"{posted['id']}. "
                        f"similarity: {difference}",
                    )
                    self._updateDiscarded()
                    # don't post it
                    return True

        post["hash"] = fingerprint["string_hash"]
        post["caption"] = fingerprint["caption"]
        return False

    def _findNew(self):  # sourcery skip: extract-method
        # Checks if new posts loaded (currently in self.posts list) are in fact
        # new or just a repost.

        logging.info("Finding new posts..")

        # load lists of files posted and discarded
        self._loadPosted()
        self._loadDiscarded()

        # clear
        self._post_queue = []  # list of posts
        self._to_discard = []  # list of posts

        # If no meme has been posted yet, there's no need to check
        if len(self._posted) == 0 and len(self._discarded) == 0:
            logging.info("No meme has been posted before!")

            fingerprint = self._imageFingerprint(
                self._posts[0]["url"],
                ocr=self._settings["ocr"],
                hash=self._settings["hash_threshold"] > 0,
            )

            self._posts[0]["hash"] = fingerprint["string_hash"]
            self._posts[0]["caption"] = fingerprint["caption"]
            self._post_queue.append(self._posts[0])

            return True

        # current loaded posts
        for post in self._posts:

            if self._isAlreadyDiscarded(post):
                continue

            if self._containsSkipWords(post):
                continue

            if self._isAlreadyPosted(post):
                continue

            self._post_queue.append(post)
            # we found a post
            return True

        return False

    def _updatePosted(self):
        # Update posted file
        try:
            with open(self._settings["posted_file"], "r") as json_file:
                posted_data = ujson.load(json_file)
        except FileNotFoundError:
            posted_data = []

        posted_data.append(self._post_queue[0])

        with open(self._settings["posted_file"], "w") as json_file:
            ujson.dump(posted_data, json_file, indent=2)

        logging.info("Posted list saved")

    def _updateDiscarded(self):
        # Update discarded file
        if not self._to_discard:
            return

        # update discarded file
        try:
            with open(self._settings["discarded_file"], "r") as json_file:
                discarded_data = ujson.load(json_file)
        except FileNotFoundError:
            discarded_data = []

        for dict in self._to_discard:
            discarded_data.append(dict)

        with open(self._settings["discarded_file"], "w") as json_file:
            ujson.dump(discarded_data, json_file, indent=2)

        logging.info("Discarded list saved")

    def fetch(self):
        # Function used to join the routine of loading posts and checking if
        # they are in fact new
        logging.info("Fetching new memes...")

        while self._loadPosts() == 0:
            # Something went wrong, we cannot load new posts. Maybe reddit is
            # down?
            logging.info("Cannot load posts... trying again in 10s")
            sleep(10)

        logging.info(f"{str(len(self._posts))} posts found!")

        while not self._findNew():
            # We didn't find any new post... let's wait a while and try again
            logging.info("Cannot find new posts... trying again in 10s")
            sleep(10)
            self._loadPosts()

        logging.info("Memes fetched")

    def cleanPosted(self):
        # Deletes the list of already posted subreddits
        logging.info("Cleaning posted data...")
        now = datetime.datetime.now()  # current date and time

        try:
            with open(self._settings["posted_file"]) as json_file:
                posted_data = ujson.load(json_file)
        except FileNotFoundError:
            logging.info("No old posted files found")
            return 0

        old_count = 0

        for posted in posted_data:
            # Loop through all the posted memes and check the time at which
            # they were posted
            posted_time = datetime.fromisoformat(posted["timestamp"])

            if (now - posted_time).days > self._settings["max_days"]:
                # if the post has been posted too long ago, it has no sense to
                # still check if it has been posted already. Let's just mark it
                # so it can be later deleted
                old_count += 1
                posted["delete"] = True

        # Let's keep all the post that are not flagged and save it to file
        new_posted_data = [x for x in posted_data if "delete" not in x]

        # write new data to file
        with open(self._settings["posted_file"], "w") as outfile:
            ujson.dump(new_posted_data, outfile, indent=2)

        logging.info("Posted data cleaned")

        return old_count

    def cleanDiscarded(self):
        # Deletes the list of already posted subreddits
        logging.info("Cleaning discarded data...")
        now = datetime.datetime.now()  # current date and time

        try:
            with open(self._settings["discarded_file"]) as json_file:
                discarded_data = ujson.load(json_file)
        except FileNotFoundError:
            logging.info("No old posted files found")
            return 0

        discarded_count = 0

        for discarded in discarded_data:
            # Loop through all the posted memes and check the time at which
            # were posted
            discarded_time = datetime.fromisoformat(discarded["timestamp"])
            if (now - discarded_time).days > self._settings["max_days"]:
                # if the post has been posted too long ago, it has no sense to
                # still check if it has been posted already. Let's just mark it
                # so it can be later deleted
                discarded_count += 1
                discarded["delete"] = True

        # Let's keep all the post that are not flagged and save it to file
        new_discarded_data = [
            x for x in discarded_data if "delete" not in x
        ]

        # write new data to file
        with open(self._settings["discarded_file"], "w") as outfile:
            ujson.dump(new_discarded_data, outfile, indent=2)

        logging.info("Posted data cleaned")

        return discarded_count

    def addPost(self, url):
        fingerprint = self._imageFingerprint(
            url,
            hash=self._settings["hash_threshold"] > 0,
            ocr=self._settings["ocr"],
        )
        self._post_queue.append({
            "url": url,
            "hash": fingerprint["string_hash"],
            "caption":  fingerprint["caption"],
        })

    def cleanQueue(self):
        self._post_queue = []

    @property
    def ocr(self):
        return self._settings["ocr"]

    @ocr.setter
    def ocr(self, value):
        self._settings["ocr"] = value
        self._saveSettings()

    @property
    def threshold(self):
        return self._settings["hash_threshold"]

    @threshold.setter
    def threshold(self, value):
        self._settings["hash_threshold"] = value
        self._saveSettings()

    @property
    def subreddits(self):
        return self._settings["subreddits"]

    @subreddits.setter
    def subreddits(self, list):
        self._settings["subreddits"] = [sub for sub in list]
        self._saveSettings()

    @property
    def wordstoskip(self):
        return self._settings["words_to_skip"]

    @wordstoskip.setter
    def wordstoskip(self, list):
        print(list)
        self._settings["words_to_skip"] = [word.lower() for word in list]
        self._saveSettings()

    @property
    def new_url(self):
        # consumes and returns new post
        if len(self._post_queue) == 0:
            return None

        self._updatePosted()
        return self._post_queue.pop(0)["url"]

    @property
    def queue(self):
        return self._post_queue

    @property
    def settings(self):
        return self._settings


def main():
    logging.basicConfig(
        filename=__file__.replace(".py", ".log"),
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        filemode="w"
    )

    r = Reddit()
    r.fetch()


if __name__ == '__main__':
    main()
