import os
import shutil
import unittest

from modules.database import Database
from modules.reddit import Reddit
from modules.fingerprinter import Fingerprinter
from modules.mediadownloader import MediaDownloader


class RedditDatabaseIntegrationTest(unittest.IsolatedAsyncioTestCase):
    _settings_path: str = "settings/settings.json"
    _temp_settings_path: str = "tests/temp_settings.json"
    _temp_folder = "tests/tmp"
    _requests_limit: int = 20
    _posts_limit: int = 5

    def _createDatabase(self):
        database = Database()
        # inject test settings
        database._loadSettings()
        database._settings["database_name"] = "integration_test"
        database._connect()
        return database

    def _clearDatabases(self):
        database = self._createDatabase()
        database._db["posts"].drop()
        database._db["fingerprints"].drop()

    def _createReddit(self):
        reddit = Reddit()
        reddit._settings_path = self._temp_settings_path
        reddit._settings["requests_limit"] = self._requests_limit
        return reddit

    def _createFingerprinter(self):
        fingerprinter = Fingerprinter()
        fingerprinter._settings_path = self._temp_settings_path
        return fingerprinter

    def _createMediaDownloader(self):
        downloader = MediaDownloader()
        downloader._settings["temp_folder"] = self._temp_folder
        downloader._createTempFolder()
        return downloader

    def setUp(self) -> None:
        shutil.copy(self._settings_path, self._temp_settings_path)

    def tearDown(self) -> None:
        os.remove(self._temp_settings_path)
        for file in os.listdir(self._temp_folder):
            os.remove(os.path.join(self._temp_folder, file))

        self._clearDatabases()

    async def testAddPosts(self):
        self._clearDatabases()
        reddit = self._createReddit()
        database = self._createDatabase()
        downloader = self._createMediaDownloader()
        fingerprinter = self._createFingerprinter()

        ids = set()
        urls = set()
        hashes = set()
        captions = set()

        posts = await reddit.fetch()

        for x in range(self._posts_limit):
            new_post = posts[x]
            media_path, media_preview = downloader.downloadMedia(new_post.url)
            new_post.path = media_path

            if media_preview is not None:
                img_path = media_preview
            else:
                img_path = media_path

            new_fingerprint = fingerprinter.fingerprint(
                img_url=new_post.url, img_path=img_path
            )

            database.addData(post=new_post, fingerprint=new_fingerprint)

            stored_data = database.stored_data
            self.assertEqual(stored_data[0], x + 1)
            self.assertEqual(stored_data[1], x + 1)

            ids.add(new_post.id)
            self.assertSetEqual(ids, database.getOldIds())
            urls.add(new_post.url)
            self.assertSetEqual(urls, database.getOldUrls())
            hashes.add(new_fingerprint.hash)
            self.assertSetEqual(hashes, database.getOldHashes())
            captions.add(new_fingerprint.caption)
            self.assertSetEqual(captions, database.getOldCaptions())

        self._clearDatabases()
