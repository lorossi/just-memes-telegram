import requests
import xmltodict
import ffmpeg
from os import remove


class VideoDownloader:
    def __init__(self):
        self._default_path = "complete.mp4"
        self._audio_path = "audio.mp4"
        self._video_path = "video.mp4"

    def _downloadMedia(self, url, dest) -> str:
        with open(dest, "wb") as f:
            f.write(requests.get(url).content)

    def _downloadVReddit(self, url) -> bool:
        r = requests.get(url + "/DASHPlaylist.mpd")

        if r.status_code != 200:
            return

        playlist = xmltodict.parse(r.content)["MPD"]["Period"]["AdaptationSet"]

        audio_representation = [p for p in playlist if p["@contentType"] == "audio"][
            -1
        ]["Representation"]
        audio_url = url + "/" + audio_representation["BaseURL"]

        video_representation = [p for p in playlist if p["@contentType"] == "video"][0][
            "Representation"
        ]
        video_url = url + "/" + [p["BaseURL"] for p in video_representation][-1]

        self._downloadMedia(audio_url, self._audio_path)
        self._downloadMedia(video_url, self._video_path)

        input_audio = ffmpeg.input("audio.mp4")
        input_video = ffmpeg.input("video.mp4")

        ffmpeg.concat(input_video, input_audio, v=1, a=1).output(
            self._default_path
        ).run(quiet=True, input=b"y")

        return self._default_path

    def downloadVideo(self, url: str) -> str:
        if "v.redd.it" in url:
            if self._downloadVReddit(url):
                return self._default_path
            return None
        else:
            # gif download is not yet implemented
            return None

    def deleteVideo(self) -> None:
        remove(self._default_path)
        remove(self._audio_path)
        remove(self._video_path)
