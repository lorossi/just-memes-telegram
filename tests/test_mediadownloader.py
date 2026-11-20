"""Unit tests for the media downloader module."""

import unittest
from http import HTTPStatus
from pathlib import Path

import ffmpeg
import pytest
import ujson
from PIL import Image

from just_memes_telegram.modules.mediadownloader import (
    MediaDownloader,
    extractLinksFromPlaylist,
    isGifUrl,
    isVideoUrl,
)
from tests.utils import DATA_FOLDER, TempSettingsMixin, createTextImage, notNone


class MediaDownloaderTest(TempSettingsMixin, unittest.IsolatedAsyncioTestCase):
    """Unit tests for the MediaDownloader class."""

    def setUp(self) -> None:
        """Create the temporary settings and the downloader."""
        self.createTempSettings()
        self.downloader = MediaDownloader(settings_path=self.settings_path)

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self.removeTempSettings()

    def _createVideo(self) -> str:
        """Create a short test video with FFMPEG.

        Returns:
            str: path of the video
        """
        path = str(self.temp_dir / "video.mp4")
        ffmpeg.input("testsrc=duration=1:size=64x64:rate=10", f="lavfi").output(
            path, pix_fmt="yuv420p"
        ).overwrite_output().run(quiet=True)
        return path

    def testProperties(self) -> None:
        """Test the properties and the string representation."""
        self.assertRegex(self.downloader.ffmpeg_version, r"^\d+(\.\d+)+$")
        self.assertEqual(repr(self.downloader), str(self.downloader))
        self.assertRegex(str(self.downloader), r"^MediaDownloader:(\n\t· [\w ]+: .+)+$")

    def testUrlTypes(self) -> None:
        """Test the detection of videos and gifs."""
        video_urls = [
            "https://v.redd.it/INVALID",
            "https://v.redd.it/INVALID/",
            "https://v.redd.it/INVALID/DASH_720.mp4",
            "https://i.imgur.com/INVALID.gifv",
            "https://i.imgur.com/INVALID.mp4?query=1",
            "https://i.redd.it/INVALID.GIF",
        ]
        image_urls = [
            "https://i.redd.it/INVALID.jpg",
            "https://i.redd.it/INVALID.png",
            "https://i.redd.it/mp4.jpeg",
            "https://i.redd.it/INVALID.jpg?width=640",
        ]
        for url in video_urls:
            self.assertTrue(isVideoUrl(url), url)
            self.assertTrue(self.downloader.isVideo(url), url)
        for url in image_urls:
            self.assertFalse(isVideoUrl(url), url)

        self.assertTrue(isGifUrl("https://i.redd.it/a.gif"))
        self.assertFalse(isGifUrl("https://i.imgur.com/a.gifv"))

    def testPlaylist(self) -> None:
        """Test the extraction of the streams from a DASH playlist."""
        base = "https://v.redd.it/abc"
        video_only = {"Representation": [{"BaseURL": "v1"}, {"BaseURL": "v2"}]}
        self.assertEqual(
            extractLinksFromPlaylist(base + "/", video_only), (f"{base}/v2", None)
        )

        with_audio = [
            {"@contentType": "video", "Representation": {"BaseURL": "v"}},
            {"@mimeType": "audio/mp4", "Representation": [{"BaseURL": "a"}]},
        ]
        self.assertEqual(
            extractLinksFromPlaylist(base, with_audio), (f"{base}/v", f"{base}/a")
        )

        audio_only = [{"@contentType": "audio", "Representation": {"BaseURL": "a"}}]
        with self.assertRaises(ValueError):
            extractLinksFromPlaylist(base, audio_only)

    def testTempFolder(self) -> None:
        """Test the cleaning of the temp folder, keeping the requested files."""
        folder = self.downloader.temp_folder
        self.assertTrue(folder.is_dir())

        keep = folder / "keep.png"
        keep.write_text("keep", encoding="utf-8")
        (folder / "delete.png").write_text("delete", encoding="utf-8")

        self.downloader.cleanTempFolder(keep=[str(keep), None])
        self.assertListEqual(list(folder.iterdir()), [keep])

        self.downloader.cleanTempFolder()
        self.assertListEqual(list(folder.iterdir()), [])

        # deleting missing files is not an error
        self.downloader.deleteFile(keep)
        self.downloader.deleteFile(None)

    def testGenerateFilename(self) -> None:
        """Test that filenames are unique and inside the temp folder."""
        names = {self.downloader._generateFilename("mp4") for _ in range(100)}
        self.assertEqual(len(names), 100)
        for name in names:
            self.assertEqual(Path(name).parent, self.downloader.temp_folder)
            self.assertTrue(name.endswith(".mp4"))
        self.assertTrue(
            self.downloader._generateFilename("png", preview=True).endswith(
                "-preview.png"
            )
        )

    def testConvertImage(self) -> None:
        """Test the conversion of an image to RGBA png."""
        path = createTextImage(self.temp_dir / "image.jpg", "memes")
        self.assertTrue(MediaDownloader._convertImage(str(path)))
        with Image.open(path) as im:
            self.assertEqual((im.format, im.mode), ("PNG", "RGBA"))

        not_an_image = self.temp_dir / "invalid.png"
        not_an_image.write_text("invalid", encoding="utf-8")
        self.assertFalse(MediaDownloader._convertImage(str(not_an_image)))

    def testExtractFirstFrame(self) -> None:
        """Test the extraction of the preview of a video."""
        preview = self.downloader._extractFirstFrame(self._createVideo())
        preview = notNone(preview)
        with Image.open(preview) as im:
            self.assertEqual((im.format, im.mode, im.size), ("PNG", "RGBA", (64, 64)))

        self.assertIsNone(self.downloader._extractFirstFrame(str(self.temp_dir / "x")))

    def testConvertGif(self) -> None:
        """Test the conversion of a gif (with odd size) to mp4."""
        gif_path = str(self.temp_dir / "image.gif")
        frames = [Image.new("RGB", (63, 31), color) for color in ("red", "blue")]
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100)

        video_path = str(self.temp_dir / "video.mp4")
        MediaDownloader._convertGif(gif_path, video_path)
        stream = ffmpeg.probe(video_path)["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (62, 30))

    async def testUnsupportedUrl(self) -> None:
        """Test that urls not matching any type are rejected without requests."""
        result = await self.downloader.downloadMedia("https://example.com/page")
        self.assertFalse(result.is_successful)
        self.assertEqual(result.status, HTTPStatus.BAD_REQUEST)
        self.assertIsNone(result.path)

    @pytest.mark.network
    async def testDownloads(self) -> None:
        """Test the download of real posts."""
        posts = ujson.loads((DATA_FOLDER / "posts.json").read_text(encoding="utf-8"))
        for url in posts["Posts"]:
            result = await self.downloader.downloadMedia(url)
            self.assertTrue(result.is_successful, url)
            self.assertTrue(Path(notNone(result.path)).exists())
            self.assertTrue(Path(notNone(result.preview_path)).exists())

    @pytest.mark.network
    async def testInvalidDownloads(self) -> None:
        """Test that invalid posts are reported as errors."""
        posts = ujson.loads((DATA_FOLDER / "posts.json").read_text(encoding="utf-8"))
        for url in posts["Invalid"]:
            result = await self.downloader.downloadMedia(url)
            self.assertFalse(result.is_successful, url)
            self.assertIsNone(result.path)
            self.assertIsNotNone(result.error)
