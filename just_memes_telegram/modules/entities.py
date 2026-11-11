"""Result objects returned by requests and downloads."""

from dataclasses import dataclass, field
from http import HTTPStatus


@dataclass(frozen=True)
class BaseEntity:
    """Common fields of every result."""

    status: int | None = None
    error: str | None = None
    url: str | None = None
    redirect_url: str | None = None

    @property
    def is_successful(self) -> bool:
        """Whether the operation completed without errors."""
        return self.status == HTTPStatus.OK and self.error is None


@dataclass(frozen=True)
class RequestResult(BaseEntity):
    """Result of an HTTP request.

    Header names are stored lowercase.
    """

    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadResult(BaseEntity):
    """Result of a download."""

    path: str | None = None
    preview_path: str | None = None
