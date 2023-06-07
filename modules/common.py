import logging

import aiofiles
import aiohttp

from modules.entities import DownloadResult, RequestResult


async def asyncRequest(url: str, download_content: bool = True) -> RequestResult:
    """Request a url.

    Args:
        url (str): url to request
        download_content (bool, optional): Whether to download the content or not.
            If False, the content will be a coroutine.
            Defaults to True.

    Returns:
        RequestResult
    """
    logging.debug(f"Requesting url: {url}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                logging.error(
                    f"Error while requesting url: {url}. Error code: {response.status}."
                )
                return RequestResult(
                    status=response.status,
                    content=None,
                    url=url,
                )

            logging.debug(f"Request successful. Status code: {response.status}.")
            if not download_content:
                return RequestResult(
                    status=response.status,
                    content=response,
                    url=url,
                    redirect_url=str(response.url),
                    headers=response.headers,
                )

            return RequestResult(
                status=response.status,
                content=await response.read(),
                url=url,
                redirect_url=str(response.url),
                headers=response.headers,
            )


async def asyncDownload(url: str, path: str) -> DownloadResult:
    """Download an image or a video by its url and return the status code.
    Args:
        url (str): content url
        path (str): path to save the content

    Returns:
        DownloadResult
    """
    logging.info(f"Attempting to download image with url: {url}.")
    r = await asyncRequest(url)
    if r.status != 200:
        logging.error(f"Cannot download image. Status code: {r.status}")
        return DownloadResult(
            status=r.status,
        )

    logging.debug(f"Download successful. Status code: {r.status}.")
    logging.debug(f"Saving image to path: {path}.")

    f = await aiofiles.open(path, mode="wb")
    await f.write(r.content)
    await f.close()

    return DownloadResult(
        status=r.status,
        path=path,
        url=url,
        redirect_url=r.redirect_url,
    )
