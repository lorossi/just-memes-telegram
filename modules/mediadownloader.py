"""Reddit Downloader class."""
from __future__ import annotations

import logging
import re
from os import listdir, makedirs, path, remove
from subprocess import PIPE, run
from time import time

import ffmpeg
import requests
import ujson
import xmltodict
from PIL import Image


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

    def _downloadImage(self, url: str, path: str = None) -> tuple[str, str]:
        if not path:
            path = self._generateFilename("png")

        downloaded_path, preview_path = self._downloadContent(url, path)
        if downloaded_path is None:
            return None, None

        self._convertImage(downloaded_path)
        return downloaded_path, preview_path

    def _downloadContent(self, url: str, path: str) -> tuple[str, str]:
        """Download an image or a video by its url and return its path.

        Args:
            url (str): content url
            path (str): path to save the content

        Returns:
            tuple[str, str]: path of the downloaded content and its preview.
        """

        r = requests.get(url)
        if r.status_code != 200:
            logging.error(f"Cannot download image. Status code: {r.status_code}")
            return None, None
        if r.url == self._imgur_removed_url:
            logging.error("Image has been removed.")
            return None, None

        try:
            with open(path, "wb") as f:
                f.write(r.content)

            return path, None
        except Exception as e:
            logging.error(f"Error while downloading media. Error: {e}.")
            return None, None

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

    def _downloadVReddit(self, url: str) -> tuple[str, str]:
        """Download a video from v.redd.it by its url.

        Args:
            url (str): url of the post

        Returns:
            tuple[str, str]: video and preview path
        """
        logging.info("Loading playlist.")

        r = requests.get(url + "/DASHPlaylist.mpd")

        logging.info(f"Status code: {r.status_code}")
        if r.status_code != 200:
            logging.error(f"Cannot download video. Status code: {r.status_code}")
            return

        # load playlist
        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
        video_url, audio_url = self._extractLinksFromPlaylist(url, playlist)

        video_path = self._generateFilename("mp4")

        if audio_url:
            # audio was found, download it separately from video
            logging.info("Downloading audio and video separately.")

            audio_part_path = self._generateFilename("mp4")
            video_part_path = self._generateFilename("mp4")

            self._downloadContent(audio_url, audio_part_path)
            self._downloadContent(video_url, video_part_path)

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
                return None, None
            except Exception as e:
                logging.error(f"Error while concatenating video. Error: {e}")
                self.deleteFile(audio_part_path)
                self.deleteFile(video_part_path)
                return None, None

        else:
            logging.info("Downloading video.")
            # no audio
            self._downloadContent(video_url, video_path)

        # extract first frame
        preview_path = self._extractFirstFrame(video_path)
        if preview_path is not None:
            return video_path, preview_path

        return None, None

    def _downloadGif(self, gif_url: str) -> tuple[str, str]:
        """Download a gif from the url.

        Args:
            gif_url (str): url of the gif

        Returns:
            tuple[str, str]: path of gif and preview path
        """
        logging.info(f"Downloading gif from {gif_url}")
        gif_path = self._generateFilename("gif")
        self._downloadContent(gif_url, gif_path)

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
            return None, None
        except Exception as e:
            logging.error(f"Error while converting video to GIF. Error: {e}")
            self.deleteFile(gif_path)
            return None, None

        # remove old file
        self.deleteFile(gif_path)

        # extract first frame
        preview_path = self._extractFirstFrame(video_path)
        if preview_path is not None:
            return video_path, preview_path

        return None, None

    def _downloadGifv(self, gifv_url: str) -> tuple[str, str]:
        """Download a gifv from the url.

        Args:
            gifv_url (str): url of the gifv

        Returns:
            tuple[str, str]: path of gifv and preview path
        """
        logging.info(f"Downloading gifv from {gifv_url}")
        gifv_url = gifv_url.replace(".gifv", ".mp4")
        gifv_path = self._generateFilename("mp4")
        self._downloadContent(gifv_url, gifv_path)

        # extract first frame
        preview_path = self._extractFirstFrame(gifv_path)
        if preview_path is not None:
            return gifv_path, preview_path

        return None, None

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

    def downloadMedia(self, url: str) -> tuple[str, str]:
        """Download a media (either a video from v.redd.it, an image or a gif).

        Args:
            url (str): media URL

        Returns:
            tuple[str, str]: path of the downloaded media and its preview.
                If the media is a video, the preview is the first frame of the video.
                If the media is an image, the preview is the image itself.
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
                path, preview_path = method(url)
                if path is not None:
                    logging.info(f"Downloading completed. Path: {path}")
                    return path, preview_path
                break

        logging.error("Cannot download. Aborting")
        return None, None

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
