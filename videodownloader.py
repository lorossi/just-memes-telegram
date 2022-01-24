import ujson
import ffmpeg
import logging
import requests
import xmltodict

from os import remove, path, makedirs


class VideoDownloader:
    def __init__(self):
        self._settings_path = "settings/settings.json"
        self._loadSettings()

    def _loadSettings(self) -> None:
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["VideoDownloader"]

        self._output_path = self._settings["temp_folder"] + "/complete.mp4"
        self._audio_path = self._settings["temp_folder"] + "/audio.mp4"
        self._video_path = self._settings["temp_folder"] + "/video.mp4"

    def _extractLinksFromPlaylist(self, url: str, playlist: dict) -> tuple[str, str]:
        """Given a playlist and a base url, extracts video and audio link (if available)

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

    def _downloadMedia(self, url: str, dest: str) -> None:
        """Downloads media from url

        Args:
            url (str): Source url
            dest (str): File destination
        """
        with open(dest, "wb") as f:
            f.write(requests.get(url).content)

    def _downloadVReddit(self, url: str) -> str:
        """Downloads a video from v.redd.it

        Args:
            url (str): Video link

        Returns:
            str: downloaded file path
        """
        logging.info(f"Loading playlist.")

        r = requests.get(url + "/DASHPlaylist.mpd")

        logging.info(f"Status code: {r.status_code}")
        if r.status_code != 200:
            return

        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]
        video_url, audio_url = self._extractLinksFromPlaylist(url, playlist)

        if audio_url:
            logging.info("Downloading audio and video separately.")

            self._downloadMedia(audio_url, self._audio_path)
            self._downloadMedia(video_url, self._video_path)

            input_audio = ffmpeg.input(self._audio_path)
            input_video = ffmpeg.input(self._video_path)

            logging.info("Concatenating audio and video.")
            ffmpeg.concat(input_video, input_audio, v=1, a=1).output(
                self._output_path
            ).run(quiet=True, input=b"y")

        else:
            logging.info("Downloading video.")
            # no audio
            self._downloadMedia(video_url, self._output_path)

        return self._output_path

    def downloadVideo(self, url: str) -> str:
        """Downloads a video from internet

        Args:
            url (str): video url

        Returns:
            str: local video path
        """
        if not path.exists(self._settings["temp_folder"]):
            logging.info("Creating folder.")
            makedirs(self._settings["temp_folder"])

        logging.info(f"Attempting to download video {url}.")

        if "v.redd.it" in url:
            if self._downloadVReddit(url):
                logging.info(f"Downloading complete. Path: {self._output_path}.")
                return self._output_path

            logging.error("Cannot download. Aborting")
            return None
        else:
            # gif download is not yet implemented
            logging.error("Url is not from v.redd.it. Aborting.")
            return None

    def deleteVideo(self) -> None:
        logging.info("Deleting old video.")
        for x in [self._output_path, self._audio_path, self._video_path]:
            try:
                remove(x)
            except FileNotFoundError:
                pass

        logging.info("Deletion completed.")

    def __str__(self) -> str:
        return "\n\t· ".join(
            [
                "VideoDownloader:",
                f"temp folder: {self._settings['temp_folder']}",
            ]
        )
