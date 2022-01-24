import ujson
import pymongo

from time import time
from imagehash import hex_to_hash
from data import Post, Fingerprint


class Database:
    def __init__(self):
        self._settings_path = "settings/settings.json"
        self._client = None  # Pymongo client
        self._loadSettings()
        self._connect()

    def _loadSettings(self):
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Database"]

    def _connect(self):
        """Creates client and connects to MongoDB database. Url is loaded from file"""
        self._client = pymongo.MongoClient(self._settings["mongodb_url"])
        self._db = self._client[self._settings["database_name"]]

    def _savePostData(self, post: Post):
        """Adds Post data to database

        Args:
            post (Post): Post object
        """
        self._db["posts"].insert_one(post.serialize())

    def _saveFingerprintData(self, fingerprint: Fingerprint):
        """Adds Fingerprint data to database

        Args:
            fingerprint (Fingerprint): Fingerprint object
        """
        self._db["fingerprints"].insert_one(fingerprint.serialize())

    def getOldIds(self) -> set[str]:
        """Loads old ids from database, either because their relative post was discarded or posted

        Returns:
            set[str]: set of strings
        """
        return {x["id"] for x in self._db["posts"].find({}, projection=["id"])}

    def getOldUrls(self) -> set[str]:
        """Loads old urls from database, either because their relative post was discarded or posted

        Returns:
            set[str]: set of strings
        """
        return {x["url"] for x in self._db["posts"].find({}, projection=["url"])}

    def getOldHashes(self) -> set[str]:
        """Loads old hashes from database, either because their relative post was discarded or posted

        Returns:
            set[str]: set of strings
        """
        return {
            hex_to_hash(x["hash_str"])
            for x in self._db["fingerprints"].find({}, projection=["hash_str"])
            if x
        }

    def getOldCaptions(self) -> set[str]:
        """Loads old captions from database, either because their relative post was discarded or posted

        Returns:
            set[str]: set of strings
        """
        return {
            x["caption"]
            for x in self._db["fingerprints"].find({}, projection=["caption"])
        }

    def addPostToDatabase(self, post: Post = None, fingerprint: Fingerprint = None):
        """Adds a post (identified by Post and Fingerprint objects) to the database

        Args:
            post (Post, optional): [description]. Post object
            fingerprint (Fingerprint, optional): [description]. Fingerprint object
        """
        if post:
            self._savePostData(post)
        if fingerprint:
            self._saveFingerprintData(fingerprint)

    def clean(self) -> tuple[int, int]:
        """Remove old instances of documents from database

        Returns:
            tuple[int, int]: Number of removed posts and fingerprints
        """
        # calculate number of seconds equal to the number of max days
        max_seconds = self._settings["max_days"] * 24 * 60 * 60
        # calculate the earliest time a document can exist
        earliest_time = time() - max_seconds
        # actually remove old documents
        posts = self._db["posts"].delete_many({"timestamp": {"$lt": earliest_time}})
        fingerprints = self._db["fingerprints"].delete_many(
            {"timestamp": {"$lt": earliest_time}}
        )
        # return number of delete documents
        return posts.deleted_count, fingerprints.deleted_count

    @property
    def is_connected(self) -> bool:
        """Returns true if the client is connected to the database server

        Returns:
            bool
        """
        try:
            self._client.server_info()
            return True
        except pymongo.ServerSelectionTimeoutError:
            return False

    @property
    def mongodb_url(self) -> str:
        if self.is_connected:
            return self._settings["mongodb_url"]
        return None

    @property
    def mongodb_version(self) -> str:
        """Returns the version of the mongodb server

        Returns:
            str
        """
        try:
            return self._client.server_info()["version"]
        except pymongo.ServerSelectionTimeoutError:
            return None

    @property
    def stored_data(self) -> tuple[int, int]:
        """Number of stored post and fingerprint documents in the database

        Returns:
            tuple[int, int]: first item is stored posts, second is stored fingerprints
        """
        return tuple(self._db[x].count_documents({}) for x in ["posts", "fingerprints"])

    def __str__(self) -> str:

        return "\n\t· ".join(
            [
                "Database:",
                f"status: {'connected' if self.is_connected else 'not connected'}",
                f"MongoDB version: {self.mongodb_version}",
                f"MongoDB url: {self.mongodb_url}",
                f"max days: {self._settings['max_days']}",
                f"stored posts and fingerprints in database: {', '.join(str(x) for x in self.stored_data)}",
            ]
        )
