"""Unit tests for the data classes."""

import random
import string
import unittest
from time import sleep

from imagehash import hex_to_hash

from just_memes_telegram.modules.data import Fingerprint, GenericData, Post


def randomStr(length: int = 10) -> str:
    """Return a random alphanumeric string.

    Args:
        length (int, optional): length of the string

    Returns:
        str: the random string
    """
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class DataTest(unittest.TestCase):
    """Unit tests for GenericData, Fingerprint and Post."""

    _tests_num: int = 10

    def testGenericData(self) -> None:
        """Test the empty data class."""
        data = GenericData()
        self.assertEqual(data.serialize(), {})
        self.assertEqual(str(data), "GenericData()")

    def testFingerprint(self) -> None:
        """Test that the fingerprint is serialized without the hash object."""
        for _ in range(self._tests_num):
            hash_str = "".join(random.choices("0123456789abcdef", k=16))
            fingerprint_dict = {
                "caption": randomStr(20),
                "hash": hex_to_hash(hash_str),
                "hash_str": hash_str,
                "url": randomStr(50),
                "timestamp": random.randint(0, 1000),
            }
            fingerprint = Fingerprint(**fingerprint_dict)

            expected = fingerprint_dict.copy()
            del expected["hash"]
            self.assertDictEqual(fingerprint.serialize(), expected)
            self.assertEqual(
                str(fingerprint),
                f"Fingerprint(caption={expected['caption']}, hash_str={hash_str}, "
                f"url={expected['url']}, timestamp={expected['timestamp']})",
            )

    def testPost(self) -> None:
        """Test the post serialization and the path setter."""
        for _ in range(self._tests_num):
            post_dict = {
                "url": randomStr(50),
                "id": randomStr(10),
                "subreddit": randomStr(10),
                "title": randomStr(20),
                "score": random.randint(0, 100),
                "timestamp": random.randint(0, 1000),
                "video": random.choice([True, False]),
                "path": randomStr(50),
            }
            post = Post(**post_dict)
            self.assertDictEqual(post.serialize(), post_dict)
            self.assertTrue(str(post).startswith(f"Post(url={post_dict['url']}, "))

            new_path = randomStr(50)
            post.setPath(new_path)
            self.assertEqual(post.path, new_path)
            self.assertTrue(str(post).endswith(f"path={new_path})"))

    def testDefaultTimestamp(self) -> None:
        """Test that the default timestamp is evaluated for each instance."""
        first = Post(url="first")
        sleep(0.01)
        second = Post(url="second")
        self.assertLess(first.timestamp, second.timestamp)

        first_fp = Fingerprint(caption=None, hash=None, hash_str=None, url="a")
        sleep(0.01)
        second_fp = Fingerprint(caption=None, hash=None, hash_str=None, url="b")
        self.assertLess(first_fp.timestamp, second_fp.timestamp)
