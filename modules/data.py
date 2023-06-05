"""Class handling Fingerprints and Posts, with the ability to be serialized."""
from dataclasses import asdict, dataclass
from time import time
from typing import Any

from imagehash import ImageHash


@dataclass
class GenericData:
    """Generalization for all data classes."""

    def serialize(self) -> dict:
        """Serialize the object to a dictionary."""
        return asdict(self)

    def __str__(self) -> str:
        """To string dunder method."""
        dict_str = ", ".join([f"{k}={v}" for k, v in self.serialize().items()])
        return f"{self.__class__.__name__}({dict_str})"


@dataclass
class Fingerprint(GenericData):
    """FingerPrint class."""

    caption: str
    hash: ImageHash
    hash_str: str
    url: str
    timestamp: float = time()

    def serialize(self) -> dict[str, Any]:
        """Serialize the object to a dictionary."""
        d = asdict(self)
        del d["hash"]
        return d


@dataclass
class Post(GenericData):
    """Post class."""

    url: str
    id: str = None
    subreddit: str = None
    title: str = None
    score: int = 0
    timestamp: float = time()
    video: bool = False
    path: str = None

    def setPath(self, path: str) -> None:
        """Set the path of an already downloaded post.

        Args:
            path (str): path of the post
        """
        self.path = path
