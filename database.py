"""Class handling all the database operations."""
import ujson
import pymongo

from time import time
from imagehash import hex_to_hash
from data import Post, Fingerprint


class Database:
    """Class to interface to MongoDB database."""

    def __init__(self):
        """Initialize the database object."""
        self._settings_path = "settings/settings.json"
        self._client = None  # Pymongo client
        self._loadSettings()
        self._connect()

    def _loadSettings(self) -> None:
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Database"]

    def _connect(self):
        """Create client and connects to MongoDB database. Url is loaded from file."""
        self._client = pymongo.MongoClient(self._settings["mongodb_url"])
        self._db = self._client[self._settings["database_name"]]

    def _savePostData(self, post: Post):
        """Add Post data to database.

        Args:
            post (Post): Post object
        """
        self._db["posts"].insert_one(post.serialize())

    def _saveFingerprintData(self, fingerprint: Fingerprint) -> None:
        """Add Fingerprint data to database.

        Args:
            fingerprint (Fingerprint): Fingerprint object
        """
        self._db["fingerprints"].insert_one(fingerprint.serialize())

    def getOldIds(self) -> set[str]:
        """Loadsold ids from database, either because their relative post was discarded or posted.

        Returns:
            set[str]: set of strings
        """
        return {x["id"] for x in self._db["posts"].find({}, projection=["id"])}

    def getOldUrls(self) -> set[str]:
        """Load old urls from database, either because their relative post was discarded or posted.

        Returns:
            set[str]: set of strings
        """
        return {x["url"] for x in self._db["posts"].find({}, projection=["url"])}

    def getOldHashes(self) -> set[str]:
        """Load old hashes from database, either \
            because their relative post was discarded or posted.

        Returns:
            set[str]: set of strings
        """
        return {
            hex_to_hash(x["hash_str"])
            for x in self._db["fingerprints"].find({}, projection=["hash_str"])
            if x
        }

    def getOldCaptions(self) -> set[str]:
        """Load old captions from database, either because \
            their relative post was discarded or posted.

        Returns:
            set[str]: set of strings
        """
        return {
            x["caption"]
            for x in self._db["fingerprints"].find({}, projection=["caption"])
        }

    def addData(self, post: Post = None, fingerprint: Fingerprint = None) -> None:
        """Add a post (either a Post or Fingerprint) to the database.

        Args:
            post (Post, optional): [description]. Post object
            fingerprint (Fingerprint, optional): [description]. Fingerprint object
        """
        if post:
            self._savePostData(post)
        if fingerprint:
            self._saveFingerprintData(fingerprint)

    def clean(self) -> tuple[int, int]:
        """Remove old instances of documents from database.

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
        """Return true if the client is connected to the database server.

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
        """Return the url of the database server."""
        if self.is_connected:
            return self._settings["mongodb_url"]
        return None

    @property
    def mongodb_version(self) -> str:
        """Return the version of the mongodb server.

        Returns:
            str
        """
        try:
            return self._client.server_info()["version"]
        except pymongo.ServerSelectionTimeoutError:
            return None

    @property
    def stored_data(self) -> tuple[int, int]:
        """Return number of stored post and fingerprint documents in the database.

        Returns:
            tuple[int, int]: first item is stored posts, second is stored fingerprints
        """
        return tuple(self._db[x].count_documents({}) for x in ["posts", "fingerprints"])

    def __str__(self) -> str:
        """Return string representation of the database object."""
        return "\n\t· ".join(
            [
                "Database:",
                f"status: {'connected' if self.is_connected else 'not connected'}",
                f"MongoDB version: {self.mongodb_version}",
                f"MongoDB url: {self.mongodb_url}",
                f"max days: {self._settings['max_days']}",
                f"stored posts in databse: {self.stored_data[0]}",
                f"stored fingerprints in database: {self.stored_data[1]}",
            ]
        )
