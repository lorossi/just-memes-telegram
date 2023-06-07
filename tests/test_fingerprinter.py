import json
import os
import random
import string
import unittest
from time import time

import imagehash
import requests
from PIL import Image

from modules.data import Fingerprint
from modules.fingerprinter import Fingerprinter


class FingerprintBaseTest:
    _tests_num: int = 100
    _img_folder: str = "tests/tmp"
    _hashes_file: str = "tests/images.json"

    def setUp(self) -> None:
        # create temp folder
        os.makedirs(self._img_folder, exist_ok=True)

    def tearDown(self) -> None:
        # delete temp folder and all its contents
        for file in os.listdir(self._img_folder):
            os.remove(os.path.join(self._img_folder, file))
        os.rmdir(self._img_folder)

    def _createRandomInvalidText(self):
        invalid_characters = ["  ", "\n", "\t"]
        valid_character = random.choice(list(string.ascii_lowercase))
        invalid_list = [i * 100 for i in invalid_characters]
        invalid_list += valid_character * 100
        random.shuffle(invalid_list)
        invalid_text = "".join(invalid_list)
        return invalid_text, valid_character

    def _createRandomValidText(self):
        valid_characters = list(string.ascii_lowercase + string.digits)
        valid_list = [i * 10 for i in valid_characters]
        random.shuffle(valid_list)
        valid_text = "".join(valid_list)
        return valid_text

    def _downloadImage(self, url: str) -> str:
        filename = int(time() * 10e6)
        extension = url.split(".")[-1]
        path = f"{self._img_folder}/{filename}.{extension}"
        r = requests.get(url)
        self.assertEqual(r.status_code, 200, "Failed to download image.")
        with open(path, "wb") as f:
            f.write(r.content)
        return path

    def _hashImage(self, path: str) -> imagehash.ImageHash:
        im = Image.open(path)
        return imagehash.dhash(im, hash_size=32)

    def _loadImageHashes(self):
        with open(self._hashes_file) as json_file:
            return json.load(json_file)["Hashes"]

    def _createImageTestCases(self):
        hash_data = self._loadImageHashes()
        for item in hash_data:
            url = item["url"]
            # skip if hash has already been calculated
            if item.get("hash") is not None:
                continue

            path = self._downloadImage(url)
            hash = self._hashImage(path)
            item["hash"] = str(hash)

        with open(self._hashes_file, "w") as json_file:
            json.dump({"Hashes": hash_data}, json_file, indent=4)


class FingerprintTest(FingerprintBaseTest, unittest.TestCase):
    def testCreation(self):
        f = Fingerprinter()
        self.assertIsInstance(f, Fingerprinter)

    def testPrivateMethods(self):
        f = Fingerprinter()

        # test invalid text
        for _ in range(self._tests_num):
            invalid_text, valid_character = self._createRandomInvalidText()
            clean_text = f._cleanText(invalid_text)
            self.assertRegex(clean_text, r"^(" + valid_character + r"\s?)+$")

        # test valid text
        for _ in range(self._tests_num):
            valid_text = self._createRandomValidText()
            self.assertEqual(f._cleanText(valid_text), valid_text)

    def testProperties(self):
        f = Fingerprinter()
        self.assertRegex(f.pytesseract_version, r"^\d+(\.\d+){2,}$")
        self.assertEqual(f.hash_size, f._settings["hash_size"])

        self.assertIsInstance(str(f), str)
        self.assertEqual(f.__repr__(), str(f))
        self.assertRegex(
            str(f),
            f.__class__.__name__ + ":" + r"\n\s*(·\s.*:\s.*\n\s*)+·\s.*:\s.*",
        )


class FingerprinterAsyncTest(FingerprintBaseTest, unittest.IsolatedAsyncioTestCase):
    async def testFingerprint(self):
        # necessary to test both url and path
        f = Fingerprinter()
        for hash_data in self._loadImageHashes():
            hash = hash_data["hash"]
            url = hash_data["url"]
            path = self._downloadImage(url)
            # test download and hash
            fp = await f.fingerprint(img_url=url)
            self.assertIsInstance(fp, Fingerprint)
            self.assertEqual(fp.hash_str, hash)
            # test hash from file
            fp = await f.fingerprint(img_url=url, img_path=path)
            self.assertIsInstance(fp, Fingerprint)
            self.assertEqual(fp.hash_str, hash)
