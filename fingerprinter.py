"""Class handling data (Videos and Images) fingerprinting."""

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
    """Class handling data (Videos and Images) fingerprinting."""

    def __init__(self):
        """Initialize the fingerprinter object."""
        self._settings_path = "settings/settings.json"
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

    def fingerprint(self, url, path=None, hash=True, ocr=True) -> Fingerprint:
        """Fingerprint an image by providing its url.

        Args:
            url (string): Image URL
            path (string, optional): Image path
            hash (bool, optional): Should the image be hashed?
            ocr (bool, optional): Should the image be scanned with OCR?

        Returns:
            Fingerprint
        """
        timestamp = time()
        try:
            if not path:
                logging.info(f"Attempting to download image with url: {url}.")
                r = requests.get(url, stream=True)
                # handle spurious Content-Encoding
                r.raw.decode_content = True
                im = Image.open(r.raw)
            else:
                logging.info(f"Attempting to fingerprint image with path: {path}.")
                # Open it in PIL
                im = Image.open(path)

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

        logging.info("Fingerprinting complete.")

        return Fingerprint(caption, hash, str(hash), url, timestamp)

    @property
    def pytesseract_version(self) -> str:
        """Return the version of the pytesseract library."""
        return str(pytesseract.get_tesseract_version()).split("\n")[0]

    def __str__(self) -> str:
        """Return string representation of the Fingerprinter object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"imagehash version: {imagehash.__version__}",
                f"pytesseract version: {pytesseract.__version__}",
                f"tesseract version: {self.pytesseract_version }",
            ]
        )
