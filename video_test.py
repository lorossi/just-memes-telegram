from reddit import Reddit
from videodownloader import VideoDownloader
import logging


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        filemode="w",
    )

    r = Reddit()
    v = VideoDownloader()

    posts = r.fetch(include_videos=True)

    for p in posts:
        v.downloadVideo(p.url)
        v.deleteVideo()


if __name__ == "__main__":
    main()
