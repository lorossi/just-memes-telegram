import random
import unittest
import string

from modules.data import Fingerprint, GenericData, Post


class DataTest(unittest.TestCase):
    _tests_num: int = 10

    def _randomStr(self, length: int = 10):
        return "".join(random.sample(string.ascii_letters + string.digits, length))

    def testGenericData(self):
        data = GenericData()
        self.assertIsInstance(data, GenericData)
        self.assertEqual(data.serialize(), {})
        self.assertEqual(str(data), "GenericData()")

    def testFingerprint(self):
        for _ in range(self._tests_num):
            caption = self._randomStr(20)
            hash = [random.randint(0, 255) for _ in range(32)]
            hash_str = "".join([hex(x)[2:] for x in hash])
            url = self._randomStr(50)
            timestamp = random.randint(0, 1000)

            fingerprint_dict = {
                "caption": caption,
                "hash": hash,
                "hash_str": hash_str,
                "url": url,
                "timestamp": timestamp,
            }
            fingerprint_dict_no_hash = fingerprint_dict.copy()
            del fingerprint_dict_no_hash["hash"]

            fingerprint = Fingerprint(**fingerprint_dict)

            self.assertIsInstance(fingerprint, Fingerprint)
            self.assertEqual(fingerprint.caption, caption)
            self.assertEqual(fingerprint.hash, hash)
            self.assertEqual(fingerprint.hash_str, hash_str)
            self.assertEqual(fingerprint.url, url)
            self.assertEqual(fingerprint.timestamp, timestamp)
            self.assertDictEqual(
                fingerprint.serialize(),
                fingerprint_dict_no_hash,
            )
            self.assertEqual(
                str(fingerprint),
                f"Fingerprint(caption={caption}, hash_str={hash_str}, "
                f"url={url}, timestamp={timestamp})",
            )

    def testPost(self):
        for _ in range(self._tests_num):
            url = self._randomStr(50)
            id = self._randomStr(10)
            subreddit = self._randomStr(10)
            title = self._randomStr(20)
            score = random.randint(0, 100)
            timestamp = random.randint(0, 1000)
            video = random.choice([True, False])
            path = self._randomStr(50)
            post_dict = {
                "url": url,
                "id": id,
                "subreddit": subreddit,
                "title": title,
                "score": score,
                "timestamp": timestamp,
                "video": video,
                "path": path,
            }
            post = Post(**post_dict)

            self.assertIsInstance(post, Post)
            self.assertEqual(post.url, url)
            self.assertEqual(post.id, id)
            self.assertEqual(post.subreddit, subreddit)
            self.assertEqual(post.title, title)
            self.assertEqual(post.score, score)
            self.assertEqual(post.timestamp, timestamp)
            self.assertEqual(post.video, video)
            self.assertEqual(post.path, path)
            self.assertDictEqual(
                post.serialize(),
                post_dict,
            )
            self.assertEqual(
                str(post),
                f"Post(url={url}, id={id}, subreddit={subreddit}, title={title}, "
                f"score={score}, timestamp={timestamp}, video={video}, path={path})",
            )

            # set path
            new_path = self._randomStr(50)
            post.setPath(new_path)

            self.assertEqual(post.path, new_path)
            self.assertEqual(
                str(post),
                f"Post(url={url}, id={id}, subreddit={subreddit}, title={title}, "
                f"score={score}, timestamp={timestamp}, video={video}, "
                f"path={new_path})",
            )
