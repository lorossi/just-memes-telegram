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
    """
     This class handles all the connections to Reddit, including:
        -Post Fetching
        -Image OCR to prevent Reddit-only memes to be posted on Telegram
        -Image Hashing to prevent reposts to be posted on Telegram
    """

    def __init__(self):
        self._posts = []  # list of fetched posts
        self._post_queue = []  # list of next post
        self._posted = []  # list of post that have been already posted
        self._discarded = []
        self._settings = {}
        self._settings_path = "settings/settings.json"
        self._loadSettings()
        self._login()

    def _loadSettings(self):
        """Loads settings from file """
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Reddit"]
        logging.info("Settings loaded")

    def _saveSettings(self):
        """Saves all settings to file """
        with open(self._settings_path) as json_file:
            old_settings = ujson.load(json_file)

        # update old dictionary
        old_settings["Reddit"].update(self._settings)

        with open(self._settings_path, "w") as outfile:
            ujson.dump(old_settings, outfile, indent=2)

    def _loadPosted(self):
        """Loads list of already posted urls """
        try:
            with open(self._settings["posted_file"]) as json_file:
                self._posted = ujson.load(json_file)
            logging.info("Loaded posted file")
            return
        except FileNotFoundError:
            logging.info("Posted file not found. Creating it.")
            self._posted = []

    def _loadDiscarded(self):
        """Loads list of already discarded urls """
        try:
            with open(self._settings["discarded_file"]) as json_file:
                self._discarded = ujson.load(json_file)
            logging.info("Loaded discarded file")
            return
        except FileNotFoundError:
            logging.info("Discarded file not found. Creating it.")
            self._discarded = []

    def _login(self):
        """Logins into Reddit app api """
        self.reddit = praw.Reddit(
            client_id=self._settings["id"],
            client_secret=self._settings["token"],
            user_agent='PC'
        )

    def _imageFingerprint(self, url, hash=True, ocr=True):
        """Fingerprints an image by providing its url

        Args:
            url (string): Image URL
            hash (bool, optional): Should the image be hashed?
            ocr (bool, optional): Should the image be scanned with OCR?

        Returns:
            [dict]: dict containing a hash, string_hash and caption
        """
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
                # remove spaces and lower
                raw_caption = pytesseract.image_to_string(im)
                raw_caption = raw_caption.lower().strip()

                if raw_caption == "":
                    # if there's no caption, save an empty string
                    caption = ""
                else:
                    # otherwise, remove all tabs and newlines
                    for remove in ["\n", "\t"]:
                        raw_caption = raw_caption.replace(remove, "")
                    # remove multiple spaces as well
                    while "  " in raw_caption:
                        raw_caption = raw_caption.replace("  ", " ")

                    # then remove all non printable characters
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
        """Loads posts from reddit

        Returns:
            [int]: [number of posts loaded]
        """
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
            if "gallery" in submission.url:
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

        # we found at leat a post
        return len(self._posts)

    def _isAlreadyDiscarded(self, post):  # sourcery skip: merge-nested-ifs
        """Checks if the post has already been discarded

        Args:
            post [dict]: Post as created by _loadPosts

        Returns:
            [boolean]
        """

        if not self._discarded:
            return False

        for discarded in self._discarded:
            if post["id"] and discarded["id"]:
                if post["id"] == discarded["id"]:
                    return True

            if post["url"] == discarded["url"]:
                return True

    def _isARepost(self, fingerprint):
        """Checks if the image has already been posted, thanks to its fingerprint

        Args:
            fingerprint [dict]: Fingerprint created from _imageFingerprint

        Returns:
            [boolen]
        """
        # sourcery skip: merge-nested-ifs
        for posted in self._posted:
            # check caption
            if fingerprint["caption"] and posted["caption"]:
                if fingerprint["caption"] == posted["caption"]:
                    return True

            # check if hashes have been calculated for both posts
            # and if so, compare them
            # old hash, has to be loaded from string
            if posted["hash"]:
                posted_hash = imagehash.hex_to_hash(posted["hash"])
                difference = fingerprint["hash"] - posted_hash

                # if the images are too similar
                if difference < self._settings["hash_threshold"]:
                    logging.info(f"Hash difference: {difference}")
                    # don't post it
                    return True

        return False

    def _containsSkipWords(self, post, fingerprint):
        # sourcery skip: return-identity
        """Checks if the post contains words to be skipped

        Args:
            post [dict]: Post as created by _loadPosts

        Returns:
            [boolean]
        """
        lower_title = post["title"].lower()

        # if the post has no title, just return false
        if lower_title and any(
            word in lower_title for word in self._settings["words_to_skip"]
        ):
            # we found one of the words to skip
            return True

        lower_caption = fingerprint["caption"].lower()
        # check if the title contains one of the words to skip
        if any(word in lower_caption for word in
               self._settings["words_to_skip"]):
            # we found one of the words to skip
            return True

        return False

    def _isAlreadyPosted(self, post):
        # sourcery skip: merge-nested-ifs
        """Checks if the post has already been posted

        Args:
            post [dict]: Post as created by _loadPosts

        Returns:
            [boolean]
        """
        for posted in self._posted:
            # check post reddit id
            if "id" in post and "id" in posted:
                if post["id"] == posted["id"]:
                    return True

            # check post url
            if post["url"] == posted["url"]:
                return True

        return False

    def _findNew(self):  # sourcery skip: extract-method
        """Find new posts from the list of already loaded posts
        inside self._post list, as loaded by self._loadPosts

        Returns:
            [boolean]: Have new posts been found?
        """
        logging.info("Finding new posts..")

        # load lists of files posted and discarded
        self._loadPosted()
        self._loadDiscarded()

        # clear
        self._post_queue = []  # list of posts
        to_discard = []  # list of posts

        # If no meme has been posted yet, there's no need to check
        if not self._posted:
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
            logging.info(
                "Trying to check if post is ok "
                f"Post url: {post['url']}"
            )

            # check if the image has already been posted
            if self._isAlreadyPosted(post):
                logging.info("It has already been posted")
                continue

            # check if the same post has already been discarded
            if self._isAlreadyDiscarded(post):
                logging.info("It has already been discarded")
                continue

            # load fingerprint
            fingerprint = self._imageFingerprint(
                post["url"],
                ocr=self._settings["ocr"],
                hash=self._settings["hash_threshold"] > 0,
            )
            logging.info("Post fingerprinted")

            if not fingerprint:
                logging.error("Couldn't fingerprint image")
                continue
            if not fingerprint["hash"]:
                logging.error("Couldn't hash image")
                continue

            # check if the image constains banned words
            if self._containsSkipWords(post, fingerprint):
                logging.info("It contains banned words")
                to_discard.append(post)
                continue

            # check if the image is a repost
            if self._isARepost(fingerprint):
                logging.info("It is a repost")
                to_discard.append(post)
                continue

            # we found a post!
            logging.info(f"Adding post {post['url']} to queue")
            # update infos about the post
            new_post = post.copy()
            new_post["hash"] = fingerprint["string_hash"]
            new_post["caption"] = fingerprint["caption"]
            self._post_queue.append(new_post)
            # update discarded list
            self._updateDiscarded(to_discard)

            return True

        # nothing found, return false
        return False

    def _updatePosted(self, posted):
        """Updates the file of already posted urls"""
        if not posted:
            return

        try:
            with open(self._settings["posted_file"], "r") as json_file:
                posted_data = ujson.load(json_file)
        except FileNotFoundError:
            posted_data = []

        posted_data.append(posted)

        with open(self._settings["posted_file"], "w") as json_file:
            ujson.dump(posted_data, json_file, indent=2)

        logging.info("Posted list saved")

    def _updateDiscarded(self, to_discard):
        """Updates the file of already discarded urls"""
        # Update discarded file
        if not to_discard:
            return

        # update discarded file
        try:
            with open(self._settings["discarded_file"], "r") as json_file:
                discarded_data = ujson.load(json_file)
        except FileNotFoundError:
            discarded_data = []

        for dict in to_discard:
            discarded_data.append(dict)

        with open(self._settings["discarded_file"], "w") as json_file:
            ujson.dump(discarded_data, json_file, indent=2)

        logging.info("Discarded list saved")

    def fetch(self):
        """
        Function used to join the routine of loading posts and checking if
        they are, in fact, new
        """
        logging.info("Fetching new memes...")

        if len(self._post_queue) > 0:
            logging.info("Meme already in queue")
            return

        while self._loadPosts() == 0:
            # Something went wrong, we cannot load new posts. Maybe reddit is
            # down?
            logging.info("Cannot load posts... trying again in 10s")
            sleep(10)

        logging.info(f"{len(self._posts)} posts found!")

        while not self._findNew():
            # We didn't find any new post... let's wait a while and try again
            logging.info("Cannot find new posts... trying again in 10s")
            sleep(10)
            self._loadPosts()

        logging.info("Memes fetched")

    def cleanPosted(self):
        """Removes old posted posts from the list file

        Returns:
            [int]: Number of posts removed from file
        """
        logging.info("Cleaning posted data...")
        now = datetime.now()  # current date and time

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
        """Removes old discarded posts from the list file

        Returns:
            [int]: Number of posts removed from file
        """
        logging.info("Cleaning discarded data...")
        now = datetime.now()  # current date and time

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
        """Add post by providing an url

        Args:
            url (string): Image url
        """
        fingerprint = self._imageFingerprint(
            url,
            hash=self._settings["hash_threshold"] > 0,
            ocr=self._settings["ocr"],
        )
        self._post_queue.append({
            "url": url,
            "hash": fingerprint["string_hash"],
            "caption":  fingerprint["caption"],
            "id": None,
            "timestamp": datetime.now().isoformat(),
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
        self._settings["words_to_skip"] = [word.lower() for word in list]
        self._saveSettings()

    @property
    def meme_url(self):
        # consumes and returns new post
        if not self._post_queue:
            return None

        self._updatePosted(self._post_queue[0])
        return self._post_queue.pop(0)["url"]

    @property
    def meme_loaded(self):
        return len(self._post_queue) > 0

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
        format="%(asctime)s %(levelname)s %(message)s",
        filemode="w"
    )

    r = Reddit()
    r.fetch()
    r.cleanPosted()
    r.cleanDiscarded()


if __name__ == '__main__':
    main()
