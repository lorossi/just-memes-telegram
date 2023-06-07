"""Reddit Downloader class."""
from __future__ import annotations

import logging
import re
from os import listdir, makedirs, path, remove
from subprocess import PIPE, run
from time import time

import ffmpeg
import ujson
import xmltodict
from PIL import Image

from modules.common import asyncDownload, asyncRequest
from modules.entities import DownloadResult


class MediaDownloader:
    """Reddit Downloader class."""

    _settings_path = "settings/settings.json"
    _imgur_removed_url = "https://i.imgur.com/removed.png"

    def __init__(self) -> MediaDownloader:
        """Initialize the class."""
        self._loadSettings()
        self._createTempFolder()

    def _loadSettings(self) -> None:
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["VideoDownloader"]

        self._gif_regex = r"^.*\.gif$"
        self._gifv_regex = r"^.*\.gifv$"
        self._video_regex = r"^.*(\.mp4)|(v\.redd\.it.*)$"
        self._image_regex = r"^.*\.(png)|(jpg)|(jpeg)$"

    def _createTempFolder(self) -> None:
        """Create a temporary folder. Path is created according to settings."""
        if not path.exists(self._settings["temp_folder"]):
            logging.info("Creating folder.")
            makedirs(self._settings["temp_folder"])

    def _generateFilename(self, extension: str, preview: bool = False) -> str:
        """Generate a filename from a type and an extension.

        Args:
            extension (str): file extension
            preview (bool, optional): True if the file is a preview, False otherwise

        Returns:
            str: file path complete with folder
        """
        timestamp = str(int(time() * 1e6))
        if preview:
            timestamp += "-preview"
        temp_folder = self._settings["temp_folder"]
        return f"{temp_folder}/{timestamp}.{extension}"

    def _extractLinksFromPlaylist(self, url: str, playlist: dict) -> tuple[str, str]:
        """Extract video and audio (if available) link from playlist and base url.

        Args:
            url (str): Base video url
            playlist (dict): Dict containing the playlist

        Returns:
            tuple[str, str]: video stream and audio stream (if found)
                if download is not successful, two None strings are returned
        """
        if isinstance(playlist, list):
            # if the playlist is a list, then there's also an audio track
            video_representations = [
                p for p in playlist if p["@contentType"] == "video"
            ][0]["Representation"]

            if isinstance(video_representations, list):
                # more than one video representation available
                video_url = url + "/" + video_representations[-1]["BaseURL"]
            else:
                # only one video representation available
                video_url = url + "/" + video_representations["BaseURL"]

            audio_representations = [
                p for p in playlist if p["@contentType"] == "audio"
            ][0]["Representation"]

            if isinstance(audio_representations, list):
                # more than one audio representation available
                audio_url = url + "/" + audio_representations[-1]["BaseURL"]
            else:
                audio_url = url + "/" + audio_representations["BaseURL"]
        else:
            # no audio track
            audio_url = None

            # check video track
            if isinstance(playlist["Representation"], list):
                video_url = url + "/" + playlist["Representation"][-1]["BaseURL"]
            else:
                video_url = url + "/" + playlist["Representation"]["BaseURL"]

        return (video_url, audio_url)

    def _convertImage(self, path: str) -> None:
        """Convert an image to png.

        Args:
            path (str): path of the image to convert
        """
        try:
            im = Image.open(path).convert("RGBA")
            im.save(path, format="png")
            im.close()
        except Exception as e:
            logging.error(f"Error while converting image. Error: {e}.")

    def _extractFirstFrame(self, path: str) -> str | None:
        """Extract the first frame of a video or the gif.

        Args:
            path (str): path of the video or the gif

        Returns:
            str | None: path of the preview or None if the preview cannot be extracted
        """
        try:
            output_path = self._generateFilename("png", preview=True)
            # create the preview
            ffmpeg.input(path).output(output_path, vframes=1).overwrite_output().run(
                quiet=True
            )
            # convert the preview to png
            self._convertImage(output_path)
            return output_path
        except Exception as e:
            logging.error(f"Error while extracting first frame. Error: {e}.")
            return None

    async def _downloadImage(self, url: str, path: str = None) -> DownloadResult:
        """Download an image from the url.

        Args:
            url (str): url of the image
            path (str, optional): path to save the image. Defaults to None.

        Returns:
            DownloadResult: download result
        """
        if not path:
            path = self._generateFilename("png")

        result = await asyncDownload(url, path)
        if result.redirect_url == self._imgur_removed_url:
            logging.error("Image was removed from Imgur.")
            self.deleteFile(path)
            return DownloadResult(
                status=404, error="Image was removed from Imgur.", redirect_url=url
            )

        if not result.is_successful:
            logging.error(f"Cannot download image. Status code: {result.status}")
            self.deleteFile(path)
            return DownloadResult(status=result.status, error="Cannot download image.")

        self._convertImage(path)
        return DownloadResult(status=200, path=path, preview_path=path)

    async def _downloadVReddit(self, url: str) -> DownloadResult:
        """Download a video from v.redd.it by its url.

        Args:
            url (str): url of the post

        Returns:
            DownloadResult: download result
        """
        logging.info("Loading playlist.")

        request_url = url + "/DASHPlaylist.mpd"
        r = await asyncRequest(request_url)

        logging.info(f"Status code: {r.status}")
        if r.status != 200:
            logging.error(f"Cannot download video. Status code: {r.status}")
            return DownloadResult(status=r.status, error="Cannot download video.")

        # load playlist
        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
        video_url, audio_url = self._extractLinksFromPlaylist(url, playlist)

        video_path = self._generateFilename("mp4")

        if audio_url:
            # audio was found, download it separately from video
            logging.info("Downloading audio and video separately.")

            audio_part_path = self._generateFilename("mp4")
            video_part_path = self._generateFilename("mp4")

            audio_download = await asyncDownload(audio_url, audio_part_path)
            video_download = await asyncDownload(video_url, video_part_path)

            if not audio_download.is_successful or not video_download.is_successful:
                self.deleteFile(audio_part_path)
                self.deleteFile(video_part_path)
                return DownloadResult(
                    status=audio_download.status,
                    error="Cannot download audio or video.",
                )

            logging.info("Concatenating audio and video.")

            # concatenate audio and video together using FFMPEG
            try:
                input_video = ffmpeg.input(video_part_path)
                input_audio = ffmpeg.input(audio_part_path)
                ffmpeg.concat(input_video, input_audio, v=1, a=1).output(
                    video_path
                ).overwrite_output().run(quiet=True)
                # remove old files
                self.deleteFile(audio_part_path)
                self.deleteFile(video_part_path)
            except ffmpeg.Error as e:
                logging.error(
                    "Error in FFMPEG while concatenating audio and video. "
                    f"Error: {e.stderr}"
                )
                self.deleteFile(audio_part_path)
                self.deleteFile(video_part_path)
                return DownloadResult(error=e.stderr)
            except Exception as e:
                logging.error(f"Error while concatenating video. Error: {e}")
                self.deleteFile(audio_part_path)
                self.deleteFile(video_part_path)
                return DownloadResult(error=str(e))

        else:
            logging.info("Downloading video.")
            # no audio
            download = await asyncDownload(video_url, video_path)
            if not download.is_successful:
                self.deleteFile(video_path)
                return download

        # extract first frame
        preview_path = self._extractFirstFrame(video_path)
        if preview_path is not None:
            return DownloadResult(
                status=200, path=video_path, preview_path=preview_path
            )

        return DownloadResult(error="Cannot extract first frame.")

    async def _downloadGif(self, gif_url: str) -> DownloadResult:
        """Download a gif from the url.

        Args:
            gif_url (str): url of the gif

        Returns:
            DownloadResult: download result
        """
        logging.info(f"Downloading gif from {gif_url}")
        gif_path = self._generateFilename("gif")
        download = await asyncDownload(gif_url, gif_path)
        if not download.is_successful:
            logging.error(f"Cannot download gif. Status code: {download.status}")
            self.deleteFile(gif_path)
            return download

        # convert gif to mp4
        logging.info("Converting gif to mp4.")
        video_path = self._generateFilename("mp4")

        try:
            ffmpeg.input(gif_path).filter(
                "scale", "trunc(in_w/2)*2", "trunc(in_h/2)*2"
            ).output(
                video_path, **{"movflags": "faststart", "pix_fmt": "yuv420p"}
            ).overwrite_output().run(
                quiet=True
            )
        except ffmpeg.Error as e:
            logging.error(
                f"Error in FFMPEG while converting video to GIF. Error: {e.stderr}"
            )
            self.deleteFile(gif_path)
            return DownloadResult(error=e.stderr)
        except Exception as e:
            logging.error(f"Error while converting video to GIF. Error: {e}")
            self.deleteFile(gif_path)
            return DownloadResult(error=str(e))

        # remove old file
        self.deleteFile(gif_path)

        # extract first frame
        preview_path = self._extractFirstFrame(video_path)
        if preview_path is not None:
            return DownloadResult(
                status=200, path=video_path, preview_path=preview_path
            )

        return DownloadResult(error="Cannot extract first frame.")

    async def _downloadGifv(self, gifv_url: str) -> DownloadResult:
        """Download a gifv from the url.

        Args:
            gifv_url (str): url of the gifv

        Returns:
            DownloadResult: download result
        """
        logging.info(f"Downloading gifv from {gifv_url}")
        gifv_url = gifv_url.replace(".gifv", ".mp4")
        gifv_path = self._generateFilename("mp4")

        download = await asyncDownload(gifv_url, gifv_path)
        if download.redirect_url == self._imgur_removed_url:
            logging.error("Gifv was removed from Imgur.")
            self.deleteFile(gifv_path)
            return DownloadResult(
                status=404, error="Gifv was removed from Imgur.", redirect_url=gifv_url
            )

        if not download.is_successful:
            logging.error(f"Cannot download gifv. Status code: {download.status}")
            self.deleteFile(gifv_path)
            return DownloadResult(status=download.status, error=download.error)

        # extract first frame
        preview_path = self._extractFirstFrame(gifv_path)
        if preview_path is not None:
            return DownloadResult(status=200, path=gifv_path, preview_path=preview_path)

        return DownloadResult(error="Cannot extract first frame.")

    def _matchExtension(self, regex: str, url: str) -> bool:
        """Check if the url matches the given regex.

        Args:
            regex (str): regex to match
            url (str): url to check

        Returns:
            bool: True if the url matches the regex, False otherwise
        """
        groups = re.search(regex, url)
        return groups is not None

    async def downloadMedia(self, url: str) -> DownloadResult:
        """Asynchronously download a media
        (either a video from v.redd.it, an image or a gif).

        Args:
            url (str): media URL

        Returns:
            DownloadResult: download result
        """
        self._createTempFolder()

        logging.info(f"Attempting to download media from {url}.")

        methods_map = {
            self._downloadGifv: self._gifv_regex,
            self._downloadGif: self._gif_regex,
            self._downloadVReddit: self._video_regex,
            self._downloadImage: self._image_regex,
        }

        for method, regex in methods_map.items():
            if self._matchExtension(regex, url):
                d = await method(url)
                if d.is_successful:
                    logging.info(f"Downloading completed. Path: {path}")
                    return d
                else:
                    logging.error(f"Cannot download. Error: {d.error}")
                    return d
                break

        logging.error("Cannot download. The url does not match any regex.")
        return DownloadResult(
            error="Cannot download. The url is not valid.", status=400
        )

    def deleteFile(self, path: str) -> None:
        """Delete a file.

        Args:
            path (str): path of the file to delete
        """
        logging.info(f"Deleting file {path}.")
        try:
            remove(path=path)
        except FileNotFoundError:
            logging.warning(f"File {path} not found.")
        except Exception as e:
            logging.error(f"Error while deleting file {path}. Error: {e}")

    def cleanTempFolder(self) -> None:
        """Delete all files in the temp folder."""
        logging.info("Cleaning temp folder.")
        temp_folder = self._settings["temp_folder"]
        for file in listdir(temp_folder):
            self.deleteFile(f"{temp_folder}/{file}")

    def isVideo(self, url: str) -> bool:
        """Check if the given URL is a video.

        Args:
            url (str): URL to check

        Returns:
            bool
        """
        return any(
            self._matchExtension(regex, url)
            for regex in [self._video_regex, self._gif_regex, self._gifv_regex]
        )

    @property
    def ffmpeg_version(self) -> str:
        """Return FFMPEG version."""
        result = run(["ffmpeg", "-version"], stdout=PIPE, stderr=PIPE).stdout.decode(
            "utf-8"
        )
        first_line = result.split("\n")[0]
        version = re.search(r"(\d+(\.\d+)+)", first_line).group(0)
        return version

    def __repr__(self) -> str:
        """Return string representation of object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"temp folder: {self._settings['temp_folder']}",
                f"gif regex: {self._gif_regex}",
                f"video regex: {self._video_regex}",
                f"image regex: {self._image_regex}",
                f"ffmpeg version: {self.ffmpeg_version}",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of object."""
        return self.__repr__()
