"""Download images, gifs and videos from Reddit and Imgur."""

import asyncio
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import uuid
from collections.abc import Awaitable, Callable, Iterable
from http import HTTPStatus
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import ffmpeg
import xmltodict
from loguru import logger
from PIL import Image

from just_memes_telegram.modules.common import (
    DEFAULT_SETTINGS_PATH,
    asyncDownload,
    asyncRequest,
    loadSettings,
)
from just_memes_telegram.modules.entities import DownloadResult

IMGUR_REMOVED_URL = "https://i.imgur.com/removed.png"

# the regexes are matched against the url, without query string and fragment
GIFV_REGEX = re.compile(r"\.gifv$", re.IGNORECASE)
GIF_REGEX = re.compile(r"\.gif$", re.IGNORECASE)
VREDDIT_REGEX = re.compile(r"^https?://v\.redd\.it/[^/]+/?$", re.IGNORECASE)
MP4_REGEX = re.compile(r"\.mp4$", re.IGNORECASE)
IMAGE_REGEX = re.compile(r"\.(png|jpe?g)$", re.IGNORECASE)

VIDEO_REGEXES = (GIFV_REGEX, GIF_REGEX, VREDDIT_REGEX, MP4_REGEX)


def _stripUrl(url: str) -> str:
    """Remove the query string and the fragment from a url.

    Args:
        url (str): the url

    Returns:
        str: the url without query string and fragment
    """
    return re.split(r"[?#]", url, maxsplit=1)[0]


def matchUrl(regex: re.Pattern[str], url: str) -> bool:
    """Check if a url matches a regex.

    Args:
        regex (re.Pattern[str]): regex to match
        url (str): url to check

    Returns:
        bool: True if the url matches the regex
    """
    return regex.search(_stripUrl(url)) is not None


def isVideoUrl(url: str) -> bool:
    """Check if a url points to a video (or to a gif, which is sent as a video).

    Args:
        url (str): url to check

    Returns:
        bool: True if the url is a video
    """
    return any(matchUrl(regex, url) for regex in VIDEO_REGEXES)


def isGifUrl(url: str) -> bool:
    """Check if a url points to a gif.

    Args:
        url (str): url to check

    Returns:
        bool: True if the url is a gif
    """
    return matchUrl(GIF_REGEX, url)


def _ffmpegError(error: ffmpeg.Error) -> str:
    """Return the decoded standard error of a failed FFMPEG run.

    Args:
        error (ffmpeg.Error): the error raised by FFMPEG

    Returns:
        str: the standard error output
    """
    stderr = error.stderr
    if isinstance(stderr, bytes):
        return stderr.decode(errors="replace")
    return str(stderr)


def _pickRepresentationUrl(adaptation_set: dict[str, Any]) -> str:
    """Return the url of the best representation of an adaptation set.

    Args:
        adaptation_set (dict[str, Any]): the adaptation set

    Returns:
        str: relative url of the last (highest quality) representation
    """
    representations = adaptation_set["Representation"]
    if isinstance(representations, list):
        representations = representations[-1]
    return representations["BaseURL"]


def _adaptationSetType(adaptation_set: dict[str, Any]) -> str | None:
    """Return the content type of an adaptation set ("video" or "audio").

    Args:
        adaptation_set (dict[str, Any]): the adaptation set

    Returns:
        str | None: the content type, if it can be determined
    """
    content_type = adaptation_set.get("@contentType")
    if content_type is None and "@mimeType" in adaptation_set:
        content_type = adaptation_set["@mimeType"].split("/")[0]
    return content_type


def extractLinksFromPlaylist(
    url: str, playlist: dict[str, Any] | list[dict[str, Any]]
) -> tuple[str, str | None]:
    """Extract video and audio (if available) links from a DASH playlist.

    Args:
        url (str): base video url
        playlist (dict | list): the "AdaptationSet" element of the playlist

    Returns:
        tuple[str, str | None]: video and audio (if found) urls

    Raises:
        ValueError: if the playlist does not contain a video stream
    """
    base_url = url.rstrip("/")
    adaptation_sets = playlist if isinstance(playlist, list) else [playlist]

    video_url = audio_url = None
    for adaptation_set in adaptation_sets:
        content_type = _adaptationSetType(adaptation_set)
        stream_url = f"{base_url}/{_pickRepresentationUrl(adaptation_set)}"
        if content_type == "audio":
            audio_url = audio_url or stream_url
        elif content_type == "video" or len(adaptation_sets) == 1:
            video_url = video_url or stream_url

    if video_url is None:
        msg = "No video stream found in the playlist."
        raise ValueError(msg)

    return video_url, audio_url


class MediaDownloader:
    """Download images, gifs and videos from Reddit and Imgur."""

    _settings: dict[str, Any]

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        """Initialize the class.

        Args:
            settings_path (str | Path, optional): path of the settings file.
        """
        self._settings = loadSettings(settings_path, "VideoDownloader")
        self._createTempFolder()

    @property
    def temp_folder(self) -> Path:
        """The folder where the files are downloaded."""
        return Path(self._settings["temp_folder"])

    def _createTempFolder(self) -> None:
        """Create the temporary folder, if it does not exist."""
        self.temp_folder.mkdir(parents=True, exist_ok=True)

    def _generateFilename(self, extension: str, *, preview: bool = False) -> str:
        """Generate a unique file path inside the temp folder.

        Args:
            extension (str): file extension
            preview (bool, optional): True if the file is a preview

        Returns:
            str: file path complete with folder
        """
        name = uuid.uuid4().hex
        if preview:
            name += "-preview"
        return str(self.temp_folder / f"{name}.{extension}")

    @staticmethod
    def _convertImage(path: str) -> bool:
        """Convert an image to RGBA png, in place. This function is blocking.

        Args:
            path (str): path of the image to convert

        Returns:
            bool: True if the conversion succeeded
        """
        try:
            with Image.open(path) as im:
                converted = im.convert("RGBA")
            converted.save(path, format="png")
        except (OSError, ValueError):
            logger.exception("Error while converting image {}.", path)
            return False
        return True

    def _extractFirstFrame(self, path: str) -> str | None:
        """Extract the first frame of a video. This function is blocking.

        Args:
            path (str): path of the video

        Returns:
            str | None: path of the preview or None if it cannot be extracted
        """
        output_path = self._generateFilename("png", preview=True)
        try:
            ffmpeg.input(path).output(output_path, vframes=1).overwrite_output().run(
                quiet=True
            )
        except ffmpeg.Error as e:
            stderr = _ffmpegError(e)
            logger.exception("Error while extracting first frame: {}", stderr)
            self.deleteFile(output_path)
            return None

        if not self._convertImage(output_path):
            self.deleteFile(output_path)
            return None
        return output_path

    async def _finalizeVideo(self, video_path: str) -> DownloadResult:
        """Extract the preview of a downloaded video and build the result.

        Args:
            video_path (str): path of the video

        Returns:
            DownloadResult: download result
        """
        preview_path = await asyncio.to_thread(self._extractFirstFrame, video_path)
        if preview_path is None:
            self.deleteFile(video_path)
            return DownloadResult(error="Cannot extract first frame.")

        return DownloadResult(
            status=HTTPStatus.OK, path=video_path, preview_path=preview_path
        )

    async def _downloadImage(self, url: str) -> DownloadResult:
        """Download an image and convert it to png.

        Args:
            url (str): url of the image

        Returns:
            DownloadResult: download result
        """
        path = self._generateFilename("png")
        result = await asyncDownload(url, path)
        if result.redirect_url == IMGUR_REMOVED_URL:
            logger.error("Image was removed from Imgur.")
            self.deleteFile(path)
            return DownloadResult(
                status=HTTPStatus.NOT_FOUND,
                error="Image was removed from Imgur.",
                redirect_url=url,
            )

        if not result.is_successful:
            logger.error("Cannot download image. Status code: {}", result.status)
            self.deleteFile(path)
            return DownloadResult(status=result.status, error="Cannot download image.")

        if not await asyncio.to_thread(self._convertImage, path):
            self.deleteFile(path)
            return DownloadResult(error="Cannot convert image.")

        return DownloadResult(status=HTTPStatus.OK, path=path, preview_path=path)

    @staticmethod
    def _mergeAudioVideo(video_path: str, audio_path: str, output_path: str) -> None:
        """Merge an audio and a video stream with FFMPEG. This function is blocking.

        Args:
            video_path (str): path of the video stream
            audio_path (str): path of the audio stream
            output_path (str): path of the merged video
        """
        input_video = ffmpeg.input(video_path)
        input_audio = ffmpeg.input(audio_path)
        ffmpeg.concat(input_video, input_audio, v=1, a=1).output(
            output_path
        ).overwrite_output().run(quiet=True)

    async def _downloadAndMerge(
        self, video_url: str, audio_url: str, video_path: str
    ) -> DownloadResult | None:
        """Download audio and video streams separately and merge them.

        Args:
            video_url (str): url of the video stream
            audio_url (str): url of the audio stream
            video_path (str): path of the merged video

        Returns:
            DownloadResult | None: the error, None if the merge succeeded
        """
        logger.info("Downloading audio and video separately.")
        audio_part_path = self._generateFilename("mp4")
        video_part_path = self._generateFilename("mp4")
        try:
            audio_download = await asyncDownload(audio_url, audio_part_path)
            video_download = await asyncDownload(video_url, video_part_path)
            if not audio_download.is_successful or not video_download.is_successful:
                return DownloadResult(
                    status=video_download.status or audio_download.status,
                    error="Cannot download audio or video.",
                )

            logger.info("Concatenating audio and video.")
            await asyncio.to_thread(
                self._mergeAudioVideo, video_part_path, audio_part_path, video_path
            )
        except ffmpeg.Error as e:
            stderr = _ffmpegError(e)
            logger.exception(
                "Error in FFMPEG while merging audio and video: {}", stderr
            )
            self.deleteFile(video_path)
            return DownloadResult(error=stderr)
        finally:
            self.deleteFile(audio_part_path)
            self.deleteFile(video_part_path)

        return None

    async def _downloadVReddit(self, url: str) -> DownloadResult:
        """Download a video from v.redd.it.

        Args:
            url (str): url of the video

        Returns:
            DownloadResult: download result
        """
        logger.info("Loading playlist.")
        r = await asyncRequest(url.rstrip("/") + "/DASHPlaylist.mpd")
        if not r.is_successful or r.content is None:
            logger.error("Cannot download playlist. Status code: {}", r.status)
            return DownloadResult(status=r.status, error="Cannot download video.")

        try:
            playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
            video_url, audio_url = extractLinksFromPlaylist(url, playlist)
        except (ExpatError, KeyError, TypeError, ValueError) as e:
            logger.exception("Cannot parse playlist.")
            return DownloadResult(error=f"Cannot parse playlist: {e}")

        video_path = self._generateFilename("mp4")

        if audio_url is None:
            logger.info("Downloading video.")
            download = await asyncDownload(video_url, video_path)
            if not download.is_successful:
                self.deleteFile(video_path)
                return download
            return await self._finalizeVideo(video_path)

        merge_error = await self._downloadAndMerge(video_url, audio_url, video_path)
        if merge_error is not None:
            return merge_error
        return await self._finalizeVideo(video_path)

    @staticmethod
    def _convertGif(gif_path: str, video_path: str) -> None:
        """Convert a gif to mp4 with FFMPEG. This function is blocking.

        Args:
            gif_path (str): path of the gif
            video_path (str): path of the converted video
        """
        ffmpeg.input(gif_path).filter(
            "scale", "trunc(in_w/2)*2", "trunc(in_h/2)*2"
        ).output(
            video_path, movflags="faststart", pix_fmt="yuv420p"
        ).overwrite_output().run(quiet=True)

    async def _downloadGif(self, gif_url: str) -> DownloadResult:
        """Download a gif and convert it to mp4.

        Args:
            gif_url (str): url of the gif

        Returns:
            DownloadResult: download result
        """
        logger.info("Downloading gif from {}", gif_url)
        gif_path = self._generateFilename("gif")
        download = await asyncDownload(gif_url, gif_path)
        if not download.is_successful:
            logger.error("Cannot download gif. Status code: {}", download.status)
            self.deleteFile(gif_path)
            return download

        logger.info("Converting gif to mp4.")
        video_path = self._generateFilename("mp4")
        try:
            await asyncio.to_thread(self._convertGif, gif_path, video_path)
        except ffmpeg.Error as e:
            stderr = _ffmpegError(e)
            logger.exception("Error in FFMPEG while converting gif: {}", stderr)
            self.deleteFile(video_path)
            return DownloadResult(error=stderr)
        finally:
            self.deleteFile(gif_path)

        return await self._finalizeVideo(video_path)

    async def _downloadVideoFile(self, url: str) -> DownloadResult:
        """Download a mp4 video (or an Imgur gifv, which is served as mp4).

        Args:
            url (str): url of the video

        Returns:
            DownloadResult: download result
        """
        logger.info("Downloading video from {}", url)
        video_url = GIFV_REGEX.sub(".mp4", _stripUrl(url))
        video_path = self._generateFilename("mp4")

        download = await asyncDownload(video_url, video_path)
        if download.redirect_url == IMGUR_REMOVED_URL:
            logger.error("Video was removed from Imgur.")
            self.deleteFile(video_path)
            return DownloadResult(
                status=HTTPStatus.NOT_FOUND,
                error="Video was removed from Imgur.",
                redirect_url=video_url,
            )

        if not download.is_successful:
            logger.error("Cannot download video. Status code: {}", download.status)
            self.deleteFile(video_path)
            return DownloadResult(status=download.status, error=download.error)

        return await self._finalizeVideo(video_path)

    async def downloadMedia(self, url: str) -> DownloadResult:
        """Download a media (a v.redd.it video, a mp4, a gif, a gifv or an image).

        Args:
            url (str): media URL

        Returns:
            DownloadResult: download result
        """
        self._createTempFolder()
        logger.info("Attempting to download media from {}.", url)

        methods: list[
            tuple[re.Pattern[str], Callable[[str], Awaitable[DownloadResult]]]
        ]
        methods = [
            (GIFV_REGEX, self._downloadVideoFile),
            (GIF_REGEX, self._downloadGif),
            (VREDDIT_REGEX, self._downloadVReddit),
            (MP4_REGEX, self._downloadVideoFile),
            (IMAGE_REGEX, self._downloadImage),
        ]

        for regex, method in methods:
            if matchUrl(regex, url):
                d = await method(url)
                if d.is_successful:
                    logger.info("Download completed. Path: {}", d.path)
                else:
                    logger.error("Cannot download. Error: {}", d.error)
                return d

        logger.error("Cannot download. The url does not match any regex.")
        return DownloadResult(
            error="Cannot download. The url is not valid.",
            status=HTTPStatus.BAD_REQUEST,
        )

    @staticmethod
    def deleteFile(path: str | Path | None) -> None:
        """Delete a file, if it exists.

        Args:
            path (str | Path | None): path of the file to delete
        """
        if path is None:
            return

        logger.debug("Deleting file {}.", path)
        try:
            Path(path).unlink()
        except FileNotFoundError:
            logger.debug("File {} not found.", path)
        except OSError:
            logger.exception("Error while deleting file {}.", path)

    def cleanTempFolder(self, keep: Iterable[str | None] = ()) -> None:
        """Delete all files in the temp folder.

        Args:
            keep (Iterable[str | None], optional): paths of files to keep,
                e.g. files of posts still in queue.
        """
        logger.info("Cleaning temp folder.")
        to_keep = {Path(p).resolve() for p in keep if p is not None}
        for file in self.temp_folder.iterdir():
            if file.is_file() and file.resolve() not in to_keep:
                self.deleteFile(file)

    @staticmethod
    def isVideo(url: str) -> bool:
        """Check if the given URL is a video.

        Args:
            url (str): URL to check

        Returns:
            bool: True if the url is a video
        """
        return isVideoUrl(url)

    @property
    def ffmpeg_version(self) -> str:
        """The FFMPEG version."""
        executable = shutil.which("ffmpeg")
        if executable is None:
            return "not installed"

        # ruff: ignore[subprocess-without-shell-equals-true]
        result = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, check=False
        )
        version = re.search(r"(\d+(\.\d+)+)", result.stdout.split("\n")[0])
        return version.group(0) if version else "unknown"

    def __repr__(self) -> str:
        """Return string representation of object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"temp folder: {self.temp_folder}",
                f"ffmpeg version: {self.ffmpeg_version}",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of object."""
        return self.__repr__()
