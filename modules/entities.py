from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class BaseEntity:
    status: int | None = None
    error: str | None = None
    url: str | None = None
    redirect_url: str | None = None


@dataclass(frozen=True)
class RequestResult(BaseEntity):
    """Class representing the result of a request."""

    content: str | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class DownloadResult(BaseEntity):
    """Class representing the result of a download."""

    path: str | None = None
    preview_path: str | None = None

    @property
    def is_successful(self) -> bool:
        return self.status == 200 and self.error is None
