from dataclasses import dataclass, asdict
from time import time


@dataclass
class GenericData:
    def serialize(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return " - ".join([f"{k}: {v}" for k, v in self.serialize().items()])


@dataclass
class Fingerprint(GenericData):
    caption: str
    hash: str
    hash_str: str
    url: str
    timestamp: int = time()

    def serialize(self):
        d = asdict(self)
        del d["hash"]
        return d


@dataclass
class Post(GenericData):
    url: str
    id: str = None
    subreddit: str = None
    title: str = None
    score: int = 0
    timestamp: int = time()
