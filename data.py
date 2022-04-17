"""Class handling Fingerprints and Posts, with the ability to be serialized."""


from dataclasses import dataclass, asdict
from time import time


@dataclass
class GenericData:
    """Generalization for all data classes."""

    def serialize(self) -> dict:
        """Serialize the object to a dictionary."""
        return asdict(self)

    def __str__(self) -> str:
        """To string dunder method."""
        return " - ".join([f"{k}: {v}" for k, v in self.serialize().items()])


@dataclass
class Fingerprint(GenericData):
    """FingerPrint class."""

    caption: str
    hash: list
    hash_str: str
    url: str
    timestamp: float = time()

    def serialize(self):
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
    path = None
