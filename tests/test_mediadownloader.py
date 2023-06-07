import json
import os
import subprocess
import unittest

from PIL import Image

from modules.mediadownloader import MediaDownloader


class MediaDownloaderBaseTest:
    _temp_folder: str = "tests/tmp"

    def setUp(self) -> None:
        os.makedirs(self._temp_folder, exist_ok=True)
        for file in os.listdir(self._temp_folder):
            os.remove(os.path.join(self._temp_folder, file))

    def tearDown(self) -> None:
        for file in os.listdir(self._temp_folder):
            os.remove(os.path.join(self._temp_folder, file))
        os.rmdir(self._temp_folder)

    def _createMediaDownloader(self):
        downloader = MediaDownloader()
        downloader._settings["temp_folder"] = self._temp_folder
        downloader._createTempFolder()
        return downloader

    def _checkFileFormat(self, path: str) -> bool:
        if ".png" in path:
            im = Image.open(path)
            im_format, im_mode = im.format, im.mode
            im.close()
            if (im_format, im_mode) != ("PNG", "RGBA"):
                return False
        # run the file command on the file
        output = subprocess.run(["file", path], capture_output=True, text=True).stdout
        # check if the format is in the output
        response = " ".join(output.split(" ")[1:]).lower()
        extension = path.split(".")[-1].lower()
        return extension in response


class MediaDownloaderTest(MediaDownloaderBaseTest, unittest.TestCase):
    def testCreation(self):
        downloader = MediaDownloader()
        self.assertIsInstance(downloader, MediaDownloader)

    def testProperties(self):
        d = self._createMediaDownloader()
        self.assertRegex(d.ffmpeg_version, r"^\d+(\.\d+)+$")

        self.assertIsInstance(str(d), str)
        self.assertEqual(d.__repr__(), str(d))
        self.assertRegex(
            str(d),
            d.__class__.__name__ + ":" + r"\n\s*(·\s.*:\s.*\n\s*)+·\s.*:\s.*",
        )

    def testIsVideo(self):
        video_urls = [
            "https://v.redd.it/INVALID",
            "https://v.redd.it/INVALID/DASH_720.mp4",
            "https://v.redd.it/INVALID/DASH_480.mp4",
            "https://i.imgur.com/INVALID.gifv",
            "https://i.imgur.com/INVALID.mp4",
        ]
        image_urls = [
            "https://i.redd.it/INVALID.jpg",
            "https://i.redd.it/INVALID.png",
        ]

        d = MediaDownloader()
        for url in video_urls:
            self.assertTrue(d.isVideo(url))

        for url in image_urls:
            self.assertFalse(d.isVideo(url))

    def testTempFolders(self):
        folder = self._temp_folder
        # delete the temp folder
        try:
            os.rmdir(folder)
        except OSError:
            pass

        # create a new downloader
        d = self._createMediaDownloader()
        # check if the temp folder exists
        self.assertTrue(os.path.exists(folder))
        # create a random file in the temp folder
        with open(f"{folder}/test.txt", "w") as f:
            f.write("test")

        d.cleanTempFolder()
        # check if the temp folder is empty
        self.assertEqual(len(os.listdir(folder)), 0)


class AsyncMediaDownloaderTest(
    MediaDownloaderBaseTest, unittest.IsolatedAsyncioTestCase
):
    async def testDownloads(self):
        # posts are saved in the posts.json file
        with open("tests/posts.json", "r") as f:
            posts = json.load(f)["Posts"]

        # create a new downloader
        d = self._createMediaDownloader()

        # download all posts
        for url in posts:
            result = await d.downloadMedia(url)
            # check if the file exists
            if result.preview_path is not None:
                self.assertTrue(os.path.exists(result.preview_path))
                self.assertTrue(self._checkFileFormat(result.preview_path))

            self.assertIsNotNone(result.path)
            self.assertTrue(os.path.exists(result.path))
            # check if the file is the correct format
            self.assertTrue(self._checkFileFormat(result.path))

    async def testInvalidDownloads(self):
        # posts are saved in the posts.json file
        with open("tests/posts.json", "r") as f:
            invalid = json.load(f)["Invalid"]

        # create a new downloader
        d = self._createMediaDownloader()

        # download all posts
        for url in invalid:
            result = await d.downloadMedia(url)
            self.assertIsNone(result.path)
            self.assertIsNone(result.preview_path)
            self.assertIsNotNone(result.error)
            self.assertNotEqual(result.status, 200)
            self.assertFalse(result.is_successful)
