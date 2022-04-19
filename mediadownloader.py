"""Reddit Downloader class."""

from xmlrpc.client import Boolean
import ujson
import ffmpeg
import logging
import requests
import xmltodict

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
        self._audio_part_path = self._settings["temp_folder"] + "/audio.mp4"
        self._video_part_path = self._settings["temp_folder"] + "/video.mp4"
        self._preview_path = self._settings["temp_folder"] + "/preview.png"

        self._video_path = self._settings["temp_folder"] + "/complete.mp4"
        self._image_path = self._settings["temp_folder"] + "/complete.png"

    def _createTempFolder(self) -> None:
        if not path.exists(self._settings["temp_folder"]):
            logging.info("Creating folder.")
            makedirs(self._settings["temp_folder"])

    def _extractLinksFromPlaylist(self, url: str, playlist: dict) -> tuple[str, str]:
        """Extract video and audio (if available) link from playlist and base url.

        Args:
            url (str): Base video url
            playlist (dict): Dict containing the playlist

        Returns:
            tuple[str, str]: video stream and audio stream (if found)
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

    def _downloadContent(self, url: str, dest: str) -> bool:
        """Download content from url.

        Args:
            url (str): content source URL
            dest (str): destination path

        Returns:
            bool: success
        """
        try:
            with open(dest, "wb") as f:
                f.write(requests.get(url).content)
                return True
        except Exception as e:
            logging.error(f"Error while downloading media. Error: {e}.")
            return False

    def _downloadVReddit(self, url: str) -> bool:
        """Download a video from v.redd.it website.

        Args:
            url (str): Video link

        Returns:
            bool: success of download
        """
        logging.info("Loading playlist.")

        r = requests.get(url + "/DASHPlaylist.mpd")

        logging.info(f"Status code: {r.status_code}")
        if r.status_code != 200:
            return

        # load playlist
        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
        video_url, audio_url = self._extractLinksFromPlaylist(url, playlist)

        if audio_url:
            # audio was found, download it separately from video
            logging.info("Downloading audio and video separately.")

            self._downloadContent(audio_url, self._audio_part_path)
            self._downloadContent(video_url, self._video_part_path)

            input_audio = ffmpeg.input(self._audio_part_path)
            input_video = ffmpeg.input(self._video_part_path)

            logging.info("Concatenating audio and video.")

            # concatenate audio and video together using FFMPEG
            try:
                ffmpeg.concat(input_video, input_audio, v=1, a=1).output(
                    self._video_path
                ).overwrite_output().run(quiet=True)
            except Exception as e:
                logging.error(f"Error while concatenating video. Error: {e}")
                return False

        else:
            logging.info("Downloading video.")
            # no audio
            self._downloadContent(video_url, self._video_path)

        # extract first frame
        try:
            logging.info("Extracting first frame")
            ffmpeg.input(self._video_path, ss=0).output(
                self._preview_path, vframes=1
            ).overwrite_output().run(quiet=True)
        except Exception as e:
            logging.error(f"Error while extracting first frame. Error: {e}")
            return False

        return True

    def downloadMedia(self, url: str) -> tuple[str, str]:
        """Download a media (either a video from v.redd.it, an image or a gif).

        Args:
            url (str): media URL

        Returns:
            tuple[str, str]: path of the downloaded media and its preview. \
                If the media is a video, the preview is the first frame of the video. \
                If the media is an image, the preview is the image itself. \
        """
        self._createTempFolder()

        logging.info(f"Attempting to download media from {url}.")

        if any(ext in url for ext in self._video_extensions):
            if self._downloadVReddit(url):
                logging.info(f"Downloading complete. Path: {self._video_path}.")
                return self._video_path, self._preview_path

        if any(ext in url for ext in self._image_extensions):
            if self._downloadContent(url, self._image_path):
                logging.info(f"Downloading complete. Path: {self._image_path}.")
                return self._image_path, self._image_path

        logging.error("Cannot download. Aborting")
        return None, None

    def deleteTempFiles(self) -> None:
        """Delete temporary files."""
        logging.info("Deleting old media.")
        for x in [
            self._audio_part_path,
            self._video_part_path,
            self._preview_path,
            self._video_path,
            self._image_path,
        ]:
            try:
                remove(x)
            except FileNotFoundError:
                pass

        logging.info("Deletion completed.")

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
