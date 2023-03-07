import logging
import tracemalloc

from modules.telegrambot import TelegramBot


def main():
    """Start the bot. Function automatically called whenever the script is run."""
    format = (
        "%(asctime)s - %(levelname)s - %(module)s (%(lineno)d, in %(funcName)s) "
        "- %(message)s"
    )
    filename = __file__.replace(".py", ".log")
    logging.basicConfig(
        level=logging.INFO,
        format=format,
        filename=filename,
        filemode="w",
    )
    tracemalloc.start()

    logging.info("Script started.")
    t = TelegramBot()
    logging.info("Telegram initialized.")
    t.start()


if __name__ == "__main__":
    main()
