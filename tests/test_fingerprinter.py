"""Unit tests for the fingerprinter module."""

import random
import string
import unittest
from pathlib import Path

import imagehash
import pytest
import ujson
from PIL import Image

from just_memes_telegram.modules.data import Fingerprint
from just_memes_telegram.modules.fingerprinter import Fingerprinter
from tests.utils import DATA_FOLDER, TempSettingsMixin, createTextImage, notNone


class FingerprinterTest(TempSettingsMixin, unittest.IsolatedAsyncioTestCase):
    """Unit tests for the Fingerprinter class."""

    _tests_num: int = 100

    def setUp(self) -> None:
        """Create the temporary settings and the fingerprinter."""
        self.createTempSettings()
        self.fingerprinter = Fingerprinter(settings_path=self.settings_path)

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self.removeTempSettings()

    def testCleanInvalidText(self) -> None:
        """Test that whitespace is collapsed and text is lowercased."""
        for _ in range(self._tests_num):
            valid_character = random.choice(string.ascii_lowercase)
            parts = [c * 100 for c in ("  ", "\n", "\t")] + [valid_character] * 100
            random.shuffle(parts)
            clean_text = Fingerprinter._cleanText("".join(parts))
            self.assertRegex(clean_text, r"^(" + valid_character + r" ?)+$")

        self.assertEqual(
            Fingerprinter._cleanText("  Hello\n\tWORLD \x00 "), "hello world "
        )

    def testCleanValidText(self) -> None:
        """Test that clean text is not changed."""
        for _ in range(self._tests_num):
            parts = [c * 10 for c in string.ascii_lowercase + string.digits]
            random.shuffle(parts)
            valid_text = "".join(parts)
            self.assertEqual(Fingerprinter._cleanText(valid_text), valid_text)

    def testProperties(self) -> None:
        """Test the properties and the string representation."""
        self.assertRegex(self.fingerprinter.tesseract_version, r"^\d+(\.\d+)+")
        self.assertEqual(self.fingerprinter.hash_size, 32)
        self.assertEqual(repr(self.fingerprinter), str(self.fingerprinter))
        self.assertRegex(
            str(self.fingerprinter), r"^Fingerprinter:(\n\t· [\w ]+: .+)+$"
        )

    async def testFingerprintPath(self) -> None:
        """Test hashing and OCR of a local image."""
        path = createTextImage(self.temp_dir / "image.png", "just memes")
        with Image.open(path) as im:
            expected_hash = imagehash.dhash(im, hash_size=32)

        fp = await self.fingerprinter.fingerprint(img_url="local", img_path=str(path))
        self.assertIsInstance(fp, Fingerprint)
        fp = notNone(fp)
        self.assertEqual(fp.hash, expected_hash)
        self.assertEqual(fp.hash_str, str(expected_hash))
        self.assertEqual(fp.url, "local")
        self.assertIsNotNone(fp.caption)
        self.assertIn("memes", fp.caption or "")

    async def testFingerprintOptions(self) -> None:
        """Test that hashing and OCR can be disabled."""
        path = str(createTextImage(self.temp_dir / "image.png", "just memes"))

        fp = await self.fingerprinter.fingerprint("local", path, ocr=False)
        fp = notNone(fp)
        self.assertIsNone(fp.caption)
        self.assertIsNotNone(fp.hash)

        fp = await self.fingerprinter.fingerprint("local", path, compute_hash=False)
        fp = notNone(fp)
        self.assertIsNone(fp.hash)
        self.assertIsNone(fp.hash_str)

    async def testFingerprintInvalid(self) -> None:
        """Test that unreadable images return None."""
        not_an_image = self.temp_dir / "image.png"
        not_an_image.write_text("this is not an image", encoding="utf-8")
        self.assertIsNone(
            await self.fingerprinter.fingerprint("local", str(not_an_image))
        )
        self.assertIsNone(
            await self.fingerprinter.fingerprint("local", str(self.temp_dir / "none"))
        )

    def testCompareHashes(self) -> None:
        """Test the comparison of equal, different and incompatible hashes."""
        h1 = imagehash.hex_to_hash("ffff0000ffff0000")
        h2 = imagehash.hex_to_hash("ffff0000ffff0001")
        h3 = imagehash.hex_to_hash("ff" * 32)
        self.assertEqual(Fingerprinter.compareHashes(h1, h1), 0)
        self.assertEqual(Fingerprinter.compareHashes(h1, h2), 1)
        self.assertEqual(Fingerprinter.compareHashes(h1, h3), float("inf"))

    @pytest.mark.network
    async def testFingerprintUrl(self) -> None:
        """Test that downloaded images match their stored hash."""
        hashes = ujson.loads(
            Path(DATA_FOLDER / "images.json").read_text(encoding="utf-8")
        )["Hashes"]
        for item in hashes:
            fp = await self.fingerprinter.fingerprint(img_url=item["url"], ocr=False)
            fp = notNone(fp)
            self.assertEqual(fp.hash_str, item["hash"])
