"""Shared helpers: settings loading and HTTP requests."""

from http import HTTPStatus
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
import ujson
from loguru import logger

from just_memes_telegram.modules.entities import DownloadResult, RequestResult

DEFAULT_SETTINGS_PATH = "settings/settings.json"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=120)


def loadSettings(settings_path: str | Path, section: str) -> dict[str, Any]:
    """Load a section of the settings file.

    Args:
        settings_path (str | Path): path of the settings file
        section (str): name of the section to load

    Returns:
        dict[str, Any]: the settings of the section
    """
    with Path(settings_path).open(encoding="utf-8") as json_file:
        return ujson.load(json_file)[section]


async def _get(url: str, *, read_content: bool) -> RequestResult:
    """Perform a GET request, without handling connection errors.

    Args:
        url (str): url to request
        read_content (bool): whether to read the response body

    Returns:
        RequestResult: result of the request
    """
    async with (
        aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session,
        session.get(url) as response,
    ):
        if response.status != HTTPStatus.OK:
            logger.error(
                "Error while requesting url: {}. Status code: {}.",
                url,
                response.status,
            )
            return RequestResult(
                status=response.status,
                error=f"Status code {response.status}",
                url=url,
                redirect_url=str(response.url),
            )

        logger.debug("Request successful. Status code: {}.", response.status)
        content = await response.read() if read_content else None
        return RequestResult(
            status=response.status,
            content=content,
            url=url,
            redirect_url=str(response.url),
            headers={k.lower(): v for k, v in response.headers.items()},
        )


async def asyncRequest(url: str, *, read_content: bool = True) -> RequestResult:
    """Request a url.

    Connection errors and timeouts are reported in the result, never raised.

    Args:
        url (str): url to request
        read_content (bool, optional): whether to download the response body.
            If False, only the status and the headers are returned.
            Defaults to True.

    Returns:
        RequestResult: result of the request
    """
    logger.debug("Requesting url: {}", url)
    try:
        return await _get(url, read_content=read_content)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.warning("Error while requesting url: {}. Error: {}", url, e)
        return RequestResult(url=url, error=str(e) or type(e).__name__)


async def asyncDownload(url: str, path: str) -> DownloadResult:
    """Download a file and save it to a path.

    Args:
        url (str): content url
        path (str): path to save the content

    Returns:
        DownloadResult: result of the download
    """
    logger.info("Attempting to download file with url: {}.", url)
    r = await asyncRequest(url)
    if not r.is_successful or r.content is None:
        logger.error("Cannot download file. Status code: {}", r.status)
        return DownloadResult(
            status=r.status,
            error=r.error or "Empty response.",
            url=url,
            redirect_url=r.redirect_url,
        )

    logger.debug("Saving file to path: {}.", path)
    async with aiofiles.open(path, mode="wb") as f:
        await f.write(r.content)

    return DownloadResult(
        status=r.status,
        path=path,
        url=url,
        redirect_url=r.redirect_url,
    )
