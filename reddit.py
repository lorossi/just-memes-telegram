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
        self._to_post = []  # list of post to be posted
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

        with open(self._settings_path, 'w') as outfile:
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
                unicode_caption = pytesseract.image_to_string(im).lower()
                if unicode_caption.strip() == "":
                    caption = ""
                else:
                    printable = set(string.printable)
                    caption = ''.join(
                        filter(lambda x: x in printable, unicode_caption)
                    )
            else:
                caption = None
            # close the image
            im.close()
        except Exception as e:
            logging.error(f'ERROR while fingerprinting {e} {url}')
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

    def _checkPostedPosts(self):
        return

    def _checkDiscardedPosts(self):
        return

    def _findNew(self):
        # ok, this is a mess
        # but fear no, I'm going to split this huge method in smaller methods
        # Checks if new posts loaded (currently in self.posts list) are in fact
        # new or just a repost.

        logging.info("Finding new posts..")

        # load lists of files posted and discarded
        self._loadPosted()
        self._loadDiscarded()

        # clear
        self._to_post = None  # single item
        self._to_discard = []  # list of posts

        # If no meme has been posted yet, there's no need to check
        if len(self._posted) == 0 and len(self._discarded) == 0:
            logging.info("No meme has been posted before!")
            fingerprint = self._imageFingerprint(
                self._posts[0]["url"],
                ocr=self._settings["ocr"],
                hash=self._settings["hash_threshold"] > 0
            )

            self._posts[0]["hash"] = fingerprint["string_hash"]
            self._posts[0]["caption"] = fingerprint["caption"]
            self._to_post = self._posts[0]
            return True

        # current loaded posts
        for post in self._posts:
            found = False

            for discarded in self._discarded:
                if post["id"] == discarded["id"]:
                    logging.info(
                        f"Post id {discarded['id']} has already been discarded"
                    )
                    found = True
                    break

                if post["url"] == discarded["url"]:
                    logging.info(
                        f"url { post['url']} has already been discarded"
                    )
                    found = True
                    break

            if found:
                continue

            if any(word.lower() in post["title"].lower() for word in self._settings["words_to_skip"]):
                # we found one of the words to skip
                logging.warning(
                    f"REPOST: title contains banned word(s). "
                    "Title:{ post['title']}"
                )
                continue

            fingerprint = self._imageFingerprint(
                post["url"],
                hash=self._settings["hash_threshold"] > 0,
                ocr=self._settings["ocr"],
            )

            if not fingerprint:
                logging.error(f"Couldn't fingerprint image {post['url']}")
                continue
            if not fingerprint["caption"]:
                logging.info(f"Post {post['id']} has no caption")

            # posted posts
            for posted in self._posted:

                posted_hash = imagehash.hex_to_hash(posted["hash"])  # old hash

                # this meme has already been posted...
                if post["id"] == posted["id"]:
                    logging.info(
                        f"Post id {posted['id']} has already been posted"
                    )
                    found = True
                    break

                if post["url"] == posted["url"]:
                    logging.info(
                        f"url {posted['url']} has already been posted"
                    )
                    found = True
                    break

                if posted["caption"] and fingerprint["caption"] and fingerprint["caption"] == posted["caption"]:
                    logging.info(
                        f"Post with caption {posted['caption']} "
                        "has already been posted"
                    )
                    found = True
                    break

                if not fingerprint["hash"]:
                    logging.info(f"Skipping hash for {post['id']}")
                # if the images are too similar
                elif fingerprint["hash"] - posted_hash < self._settings["hash_threshold"]:
                    found = True
                    self._to_discard.append(post)
                    # it's a repost
                    logging.warning(
                        f"REPOST: {post['id']} is too similar to "
                        f"{posted['id']}. "
                        f"similarity: {str(fingerprint['hash']-posted_hash)}",
                    )
                    # don't post it
                    break

                elif fingerprint["caption"] and posted["caption"] and any(word.lower() in fingerprint["caption"].lower() for word in self.words_to_skip):
                    found = True
                    self._to_discard.append(post)
                    # we found one of the words to skip
                    logging.warning("REPOST: %s contains banned word(s). Complete caption: %s",
                                    post["id"], fingerprint["caption"].lower())
                    break

            if not found:
                post["hash"] = fingerprint["string_hash"]
                post["caption"] = fingerprint["caption"]
                self._to_post = post
                return True

        # no new posts...
        return False

    def _updatePosted(self):
        # THIS HAS TO BE CALLED WITH THE GETTER FOR THE NEW POST
        # Update posted file
        try:
            with open(self._settings["posted_file"], "r") as json_file:
                posted_data = ujson.load(json_file)
        except FileNotFoundError:
            posted_data = []

        posted_data.append(self._to_post)

        with open(self._settings["posted_file"], "w") as json_file:
            ujson.dump(posted_data, json_file, indent=2)

        logging.info("Posted list saved")

    def _updateDiscarded(self):
        # THIS HAS TO BE CALLED WITH THE GETTER FOR THE NEW POST

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

        with open(self.posted_full_path, 'w') as outfile:
            ujson.dump(new_posted_data, outfile, indent=2)

        return old_count

    def cleanDiscarded(self):
        # TODO CLEAN THIS
        # Deletes the list of already posted subreddits
        logging.info("Cleaning discarded data...")
        now = datetime.datetime.now()  # current date and time

        with open(self.posted_full_path) as json_file:
            discarded_data = ujson.load(json_file)

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
        new_discarded_data = [x for x in discarded_data if "delete" not in x]
        with open(self.discarded_full_path, 'w') as outfile:
            ujson.dump(new_discarded_data, outfile, indent=2)

        return discarded_count

    def toggleOcr(self):
        # TODO replace with setter
        self.ocr = not self.ocr
        self._settings["ocr"] = not self._settings["ocr"]

    def setThreshold(self, threshold):
        # TODO replace with setter
        self.hash_threshold = threshold
        self._settings["hash_threshold"] = threshold

    def setSubreddits(self, subreddits):
        # TODO replace with setter
        # Sets the new list of subreddits and saves it to file
        self.subreddits = []
        for subreddit in subreddits:
            self.subreddits.append(subreddit)

        self._settings["subreddits"] = self.subreddits

    def setWordstoskip(self, wordstoskip):
        # TODO replace with setter
        self.words_to_skip = []
        for word in wordstoskip:
            self.words_to_skip.append(word)

        self._settings["words_to_skip"] = self.words_to_skip

    # TODO ADD GETTER FOR self._to_post


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
