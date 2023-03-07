import logging

from modules.telegrambot import TelegramBot


def main():
    """Start the bot. Function automatically called whenever the script is run."""
    logging.basicConfig(
        filename=__file__.replace(".py", ".log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        filemode="w",
    )

    logging.info("Script started.")
    t = TelegramBot()
    logging.info("Telegram initialized.")
    t.start()


if __name__ == "__main__":
    main()
