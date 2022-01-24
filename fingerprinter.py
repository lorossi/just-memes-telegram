import ujson
import logging
import requests
import imagehash
import pytesseract

from PIL import Image
from time import time
from data import Fingerprint
from string import printable


class Fingerprinter:
    def __init__(self):
        self._settings_path = "settings/settings.json"
        self._loadSettings()

    def _loadSettings(self):
        """Loads settings from file"""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Fingerprinter"]

    def _cleanText(self, text: str) -> str:
        """Cleans text by removing multiple spaces and unprintable characters

        Args:
            text (str)

        Returns:
            str
        """

        clean = text.strip().lower()
        to_replace = {"  ", "\n", "\t"}
        while any(r in clean for r in to_replace):
            for r in to_replace:
                clean = clean.replace(r, " ")

        return "".join([c if c in printable else "" for c in clean])

    def fingerprint(self, url, hash=True, ocr=True):
        """Fingerprints an image by providing its url

        Args:
            url (string): Image URL
            hash (bool, optional): Should the image be hashed?
            ocr (bool, optional): Should the image be scanned with OCR?

        Returns:
            Post
        """
        timestamp = time()
        try:
            logging.info(f"Attempting to fingerprint image with url: {url}.")
            r = requests.get(url, stream=True)
            # handle spurious Content-Encoding
            r.raw.decode_content = True
            # Open it in PIL
            im = Image.open(r.raw)
            # Hash it
            hash = imagehash.phash(im) if hash else None
            # OCR it
            if ocr:
                # remove spaces and lower
                raw_caption = pytesseract.image_to_string(im)
                caption = self._cleanText(raw_caption)
            # close the image
            im.close()

        except Exception as e:
            logging.error(f"While fingerprinting. Error: {e}.")
            return None

        return Fingerprint(caption, hash, str(hash), url, timestamp)

    @property
    def settings(self) -> dict:
        return self._settings

    def __str__(self) -> str:
        return "Fingerprinter:" f"\n\timagehash version: {imagehash.__version__}"
