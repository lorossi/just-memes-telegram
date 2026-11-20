"""Class handling images fingerprinting."""

import asyncio
from io import BytesIO
from pathlib import Path
from string import printable
from time import time
from typing import Any

import imagehash
import pytesseract
from loguru import logger
from PIL import Image

from just_memes_telegram.modules.common import (
    DEFAULT_SETTINGS_PATH,
    asyncRequest,
    loadSettings,
)
from just_memes_telegram.modules.data import Fingerprint


class Fingerprinter:
    """Class handling images fingerprinting (perceptual hash and OCR)."""

    _settings: dict[str, Any]

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        """Initialize the fingerprinter object.

        Args:
            settings_path (str | Path, optional): path of the settings file.
        """
        self._settings = loadSettings(settings_path, "Fingerprinter")
        if tesseract_path := self._settings.get("tesseract_path"):
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

    @staticmethod
    def _cleanText(text: str) -> str:
        """Clean text by removing multiple spaces and unprintable characters.

        Args:
            text (str): text to clean

        Returns:
            str: the cleaned text
        """
        clean = " ".join(text.strip().lower().split())
        return "".join(c for c in clean if c in printable)

    def _fingerprintImage(
        self, source: str | BytesIO, *, compute_hash: bool, ocr: bool
    ) -> tuple[imagehash.ImageHash | None, str | None]:
        """Hash and OCR an image. This function is blocking.

        Args:
            source (str | BytesIO): path or content of the image
            compute_hash (bool): whether to hash the image
            ocr (bool): whether to scan the image with OCR

        Returns:
            tuple[ImageHash | None, str | None]: hash and caption of the image
        """
        with Image.open(source) as im:
            img_hash = (
                imagehash.dhash(im, hash_size=self.hash_size) if compute_hash else None
            )
            caption = self._cleanText(pytesseract.image_to_string(im)) if ocr else None
        return img_hash, caption

    async def fingerprint(
        self,
        img_url: str,
        img_path: str | None = None,
        *,
        compute_hash: bool = True,
        ocr: bool = True,
    ) -> Fingerprint | None:
        """Fingerprint an image by providing its url or its path.

        Args:
            img_url (str): url of the image. It is downloaded if no path is provided.
            img_path (str, optional): path of the already downloaded image.
            compute_hash (bool, optional): should the image be hashed?
            ocr (bool, optional): should the image be scanned with OCR?

        Returns:
            Fingerprint | None: the fingerprint, None if the image cannot be read
        """
        timestamp = time()
        source: str | BytesIO
        if img_path is None:
            logger.info("Attempting to download image with url: {}.", img_url)
            request = await asyncRequest(img_url)
            if not request.is_successful or request.content is None:
                logger.error(
                    "Error while downloading image with url: {}. Status: {}.",
                    img_url,
                    request.status,
                )
                return None
            source = BytesIO(request.content)
        else:
            logger.info("Attempting to fingerprint image with path: {}.", img_path)
            source = img_path

        try:
            img_hash, caption = await asyncio.to_thread(
                self._fingerprintImage, source, compute_hash=compute_hash, ocr=ocr
            )
        except (OSError, ValueError, pytesseract.TesseractError):
            logger.exception("Error while fingerprinting image {}.", img_url)
            return None

        logger.info("Fingerprinting complete.")
        return Fingerprint(
            caption=caption,
            hash=img_hash,
            hash_str=str(img_hash) if img_hash is not None else None,
            url=img_url,
            timestamp=timestamp,
        )

    @property
    def tesseract_version(self) -> str:
        """The version of the tesseract executable."""
        return str(pytesseract.get_tesseract_version())

    @property
    def hash_size(self) -> int:
        """The size of the hash."""
        return self._settings["hash_size"]

    def __repr__(self) -> str:
        """Return string representation of the Fingerprinter object."""
        return "\n\t· ".join(
            [
                f"{self.__class__.__name__}:",
                f"imagehash version: {imagehash.__version__}",
                f"pytesseract version: {pytesseract.__version__}",
                f"tesseract version: {self.tesseract_version}",
                f"hash size: {self.hash_size}",
            ]
        )

    def __str__(self) -> str:
        """Return string representation of the Fingerprinter object."""
        return self.__repr__()

    @staticmethod
    def compareHashes(hash1: imagehash.ImageHash, hash2: imagehash.ImageHash) -> float:
        """Compare two hashes.

        Args:
            hash1 (imagehash.ImageHash): first hash
            hash2 (imagehash.ImageHash): second hash

        Returns:
            float: distance between the two hashes, the lower the more similar.
                Hashes that cannot be compared (e.g. different sizes) are
                considered infinitely distant.
        """
        try:
            return float(abs(hash1 - hash2))
        except TypeError as e:
            logger.warning("Cannot compare hashes. Error: {}.", e)
            return float("inf")
