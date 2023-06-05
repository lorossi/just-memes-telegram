import logging
import tracemalloc
import asyncio

from modules.telegrambot import TelegramBot


async def idle():
    """Do nothing. This is a coroutine that is used to keep the event loop running."""
    while True:
        await asyncio.sleep(1)


async def main():
    """Start the bot. Function automatically called whenever the script is run."""
    format = (
        "%(asctime)s - %(levelname)s - %(module)s (%(lineno)d, in %(funcName)s) "
        "- %(message)s"
    )
    logging.basicConfig(
        level=logging.INFO,
        format=format,
        filemode="w",
        filename=__file__.replace(".py", ".log"),
    )
    tracemalloc.start()

    logging.info("Script started.")
    t = TelegramBot()

    logging.info("Starting bot...")
    await t.startAsync()
    logging.info("Bot started.")

    try:
        await idle()
    except asyncio.exceptions.CancelledError:
        logging.warning("SIGINT received. Stopping bot...")
        await t.stopAsync()

    logging.info("Script ended.")


if __name__ == "__main__":
    asyncio.run(main())
