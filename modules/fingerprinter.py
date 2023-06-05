"""Class handling data (Videos and Images) fingerprinting."""
from __future__ import annotations

import logging
from string import printable
from time import time
from typing import Any

import imagehash
import pytesseract
import requests
import ujson
from PIL import Image

from .data import Fingerprint


class Fingerprinter:
    """Class handling data (Videos and Images) fingerprinting."""

    _settings_path: str = "settings/settings.json"
    _settings: dict[str, Any]

    def __init__(self) -> Fingerprinter:
        """Initialize the fingerprinter object."""
        self._loadSettings()

    def _loadSettings(self):
        """Load settings from file."""
        with open(self._settings_path) as json_file:
            self._settings = ujson.load(json_file)["Fingerprinter"]

    def _cleanText(self, text: str) -> str:
        """Clean text by removing multiple spaces and unprintable characters.

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

    def fingerprint(self, img_url, img_path=None, hash=True, ocr=True) -> Fingerprint:
        """Fingerprint an image by providing its url.

        Args:
            img_url (string): Image URL
            img_path (string, optional): Image path
            hash (bool, optional): Should the image be hashed?
            ocr (bool, optional): Should the image be scanned with OCR?

        Returns:
            Fingerprint
        """
        timestamp = time()
        try:
            if not img_path:
                logging.info(f"Attempting to download image with url: {img_url}.")
                r = requests.get(img_url, stream=True)
                # handle spurious Content-Encoding
                r.raw.decode_content = True
                im = Image.open(r.raw)
            else:
                logging.info(f"Attempting to fingerprint image with path: {img_path}.")
                # Open it in PIL
                im = Image.open(img_path)

            # Hash it
            if hash:
                img_hash = imagehash.dhash(im, hash_size=self.hash_size)
            else:
                img_hash = None
            # OCR it
            if ocr:
                # remove spaces and lower
                raw_caption = pytesseract.image_to_string(im)
                img_caption = self._cleanText(raw_caption)
            else:
                img_caption = None
            # close the image
            im.close()

        except Exception as e:
            logging.error(f"Error while fingerprinting. Error: {e}.")
            return None

        logging.info("Fingerprinting complete.")

        return Fingerprint(img_caption, img_hash, str(img_hash), img_url, timestamp)

    @property
    def pytesseract_version(self) -> str:
        """Return the version of the pytesseract library."""
        return pytesseract.get_tesseract_version().base_version

    @property
    def hash_size(self) -> int:
        return self._settings["hash_size"]

    def __repr__(self) -> str:
        """Return string representation of the Fingerprinter object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"imagehash version: {imagehash.__version__}",
                f"pytesseract version: {pytesseract.__version__}",
                f"tesseract version: {self.pytesseract_version }",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of the Fingerprinter object."""
        return self.__repr__()
