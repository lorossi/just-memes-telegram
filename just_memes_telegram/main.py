"""Entry point of the bot."""

import asyncio
import inspect
import logging
import os
import signal
import sys
from typing import override

from loguru import logger

from just_memes_telegram.modules.telegrambot import TelegramBot


class InterceptHandler(logging.Handler):
    """Forward the records of the standard logging module to loguru.

    Libraries (python-telegram-bot, httpx, asyncpraw, ...) use the standard
    logging module: this handler makes their logs consistent with the bot ones.
    """

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Send a record to loguru, keeping its level and caller."""
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # find the caller of the logging call, skipping the logging module frames
        frame, depth = inspect.currentframe(), 0
        while frame is not None and (
            depth == 0 or frame.f_code.co_filename == logging.__file__
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setupLogging() -> None:
    """Configure loguru and route the standard logging records to it."""
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    # httpx logs every Telegram polling request at INFO level
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> bool:
    """Set up the logger, start the bot and wait until it is stopped.

    Returns:
        bool: True if the bot must be restarted, False otherwise
    """
    setupLogging()

    logger.info("Starting bot...")
    t = TelegramBot()
    await t.start(wait_connection=True)

    # stop the bot gracefully on ctrl+c and when the container is stopped
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, t.requestStop)

    try:
        await t.waitForStopRequest()
        logger.info("Stop requested, stopping the bot...")
    finally:
        await t.stop()

    return t.restart_requested


def run() -> None:
    """Run the bot, restarting the whole process if requested."""
    if asyncio.run(main()):
        logger.warning("Restarting the bot...")
        # replace the current process with a fresh one, same arguments
        # ruff: ignore[start-process-with-no-shell]
        os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    run()
