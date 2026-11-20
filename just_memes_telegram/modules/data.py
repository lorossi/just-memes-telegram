"""Fingerprints and Posts, with the ability to be serialized."""

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any

from imagehash import ImageHash


@dataclass
class GenericData:
    """Generalization for all data classes."""

    def serialize(self) -> dict[str, Any]:
        """Serialize the object to a dictionary.

        Returns:
            dict[str, Any]: the object fields
        """
        return asdict(self)

    def __str__(self) -> str:
        """Return the string representation of the object."""
        dict_str = ", ".join(f"{k}={v}" for k, v in self.serialize().items())
        return f"{self.__class__.__name__}({dict_str})"


@dataclass
class Fingerprint(GenericData):
    """Fingerprint of an image: perceptual hash and OCR caption."""

    caption: str | None
    hash: ImageHash | None
    hash_str: str | None
    url: str
    timestamp: float = field(default_factory=time)

    def serialize(self) -> dict[str, Any]:
        """Serialize the object to a dictionary, without the hash object.

        Returns:
            dict[str, Any]: the object fields, except the hash
        """
        d = asdict(self)
        del d["hash"]
        return d


@dataclass
class Post(GenericData):
    """A post, either fetched from Reddit or added manually."""

    url: str
    id: str | None = None
    subreddit: str | None = None
    title: str | None = None
    score: int = 0
    timestamp: float = field(default_factory=time)
    video: bool = False
    path: str | None = None

    def setPath(self, path: str) -> None:
        """Set the path of an already downloaded post.

        Args:
            path (str): path of the post
        """
        self.path = path
