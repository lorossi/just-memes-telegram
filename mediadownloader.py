"""Reddit Downloader class."""

import ujson
import ffmpeg
import logging
import requests
import xmltodict

from time import time
from os import remove, path, makedirs


class MediaDownloader:
    """Reddit Downloader class."""

    def __init__(self):
        """Initialize the class."""
        self._settings_path = "settings/settings.json"
        self._loadSettings()

    def _loadSettings(self) -> None:
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["VideoDownloader"]

        self._image_extensions = [".jpg", ".jpeg", ".png"]
        self._video_extensions = ["v.redd.it"]
        self._download_folder = self._settings["temp_folder"]
        self._preview_path = self._settings["temp_folder"] + "/preview.png"

    def _createTempFolder(self) -> None:
        """Create a temporary folder. Path is created according to settings."""
        if not path.exists(self._settings["temp_folder"]):
            logging.info("Creating folder.")
            makedirs(self._settings["temp_folder"])

    def _generateFilename(self, type: str, extension: str) -> str:
        """Generate a filename from a type and an extension.

        Args:
            type (str): type of the file (audio, video, image, ...)
            extension (str): file extension

        Returns:
            str: file path complete with folder
        """
        timestamp = int(time() * 1e6)
        return f"{self._download_folder}/{timestamp}-{type}.{extension}"

    def _extractLinksFromPlaylist(self, url: str, playlist: dict) -> tuple[str, str]:
        """Extract video and audio (if available) link from playlist and base url.

        Args:
            url (str): Base video url
            playlist (dict): Dict containing the playlist

        Returns:
            tuple[str, str]: video stream and audio stream (if found) \
                if download is not successful, two None strings are returned
        """
        if isinstance(playlist, list):
            # if the playlist is a list, then there's also an audio track
            video_representations = [
                p for p in playlist if p["@contentType"] == "video"
            ][0]["Representation"]

            if isinstance(video_representations, list):
                # more than one video represention available
                video_url = url + "/" + video_representations[-1]["BaseURL"]
            else:
                # only  one video represention available
                video_url = url + "/" + video_representations["BaseURL"]

            audio_representations = [
                p for p in playlist if p["@contentType"] == "audio"
            ][0]["Representation"]

            if isinstance(audio_representations, list):
                # more than one audio represention available
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

    def _downloadContent(self, url: str, path: str) -> str:
        """Download content by its url and return its path.

        Args:
            url (str): content url
            path (str): path to save the content

        Returns:
            str: content path if download is successful, None otherwise
        """
        try:
            with open(path, "wb") as f:
                f.write(requests.get(url).content)
                return path
        except Exception as e:
            logging.error(f"Error while downloading media. Error: {e}.")
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
            return

        # load playlist
        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
        video_url, audio_url = self._extractLinksFromPlaylist(url, playlist)

        video_path = self._generateFilename("video", "mp4")

        if audio_url:
            # audio was found, download it separately from video
            logging.info("Downloading audio and video separately.")

            audio_part_path = self._generateFilename("audio-part", "mp4")
            video_part_path = self._generateFilename("video-part", "mp4")

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
                remove(audio_part_path)
                remove(video_part_path)
            except ffmpeg.Error as e:
                logging.error(
                    f"Error while concatenating audio and video. Error: {e.stderr}"
                )
                remove(audio_part_path)
                remove(video_part_path)
                return None, None
            except Exception as e:
                logging.error(f"Error while concatenating video. Error: {e}")
                remove(audio_part_path)
                remove(video_part_path)
                return None, None

        else:
            logging.info("Downloading video.")
            # no audio
            self._downloadContent(video_url, video_path)

        # extract first frame
        try:
            logging.info("Extracting first frame")
            ffmpeg.input(video_path, ss=0).output(
                self._preview_path, vframes=1
            ).overwrite_output().run(quiet=True)
        except Exception as e:
            logging.error(
                f"Error while extracting first frame. Error: {e}. ffmpeg error: {ffmpeg.Error}"
            )
            return None, None

        return video_path, self._preview_path

    def downloadMedia(self, url: str) -> tuple[str, str]:
        """Download a media (either a video from v.redd.it, an image or a gif).

        Args:
            url (str): media URL

        Returns:
            tuple[str, str]: path of the downloaded media and its preview. \
                If the media is a video, the preview is the first frame of the video. \
                If the media is an image, the preview is the image itself.
        """
        self._createTempFolder()

        logging.info(f"Attempting to download media from {url}.")

        if any(ext in url for ext in self._video_extensions):
            video_path, preview_path = self._downloadVReddit(url)
            if video_path:
                logging.info(f"Downloading complete. Path: {video_path}.")
                return video_path, preview_path

        if any(ext in url for ext in self._image_extensions):
            image_path = self._downloadContent(url, self._image_path)
            if image_path:
                logging.info(f"Downloading complete. Path: {image_path}.")
                return image_path, image_path

        logging.error("Cannot download. Aborting")
        return None, None

    def deleteFile(self, path: str) -> None:
        """Delete a file.

        Args:
            path (str): path of the file to delete
        """
        try:
            remove(path)
        except FileNotFoundError:
            logging.info(f"File {path} not found.")
        except Exception as e:
            logging.error(f"Error while deleting file {path}. Error: {e}")

    def __str__(self) -> str:
        """Return string representation of object."""
        return "\n\t· ".join(
            [
                "VideoDownloader:",
                f"temp folder: {self._settings['temp_folder']}",
                f"video extensions: {self._video_extensions}",
                f"image extensions: {self._image_extensions}",
            ]
        )

    def isVideo(self, url: str) -> bool:
        """Check if the given URL is a video.

        Args:
            url (str): URL to check

        Returns:
            bool
        """
        return any(ext in url for ext in self._video_extensions)
