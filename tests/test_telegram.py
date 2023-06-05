from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from modules.telegrambot import TelegramBot
from modules.database import Database


class TelegramBotTest(unittest.TestCase):
    def testCreation(self):
        bot = TelegramBot()
        self.assertIsInstance(bot, TelegramBot)

    def testProperties(self):
        bot = TelegramBot()
        words_to_skip = sorted(s.lower() for s in bot._settings["words_to_skip"])
        words_to_skip_str = ", ".join(words_to_skip)

        self.assertEqual(bot.words_to_skip, words_to_skip)
        self.assertEqual(bot.words_to_skip_str, words_to_skip_str)


class TelegramAsyncBotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        d = Database()
        d.deleteAll()

    def tearDown(self) -> None:
        d = Database()
        d.deleteAll()

    def _createMockContext(self):
        context = MagicMock()
        context.bot = MagicMock()
        context.bot.send_message = MagicMock()
        return context

    def _createMockUpdate(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = MagicMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = 123456789

        return update

    def _createSilentTelegramBot(self):
        bot = TelegramBot()
        bot._settings["admins"] = []
        return bot

    async def testCreation(self):
        bot = TelegramBot()
        self.assertIsInstance(bot, TelegramBot)

        await bot.startAsync()

        bot_mock = AsyncMock()
        bot_mock.send_message = AsyncMock()
        bot._application.bot = bot_mock

        # await a few seconds to make sure the bot is ready
        await asyncio.sleep(3)

        self.assertIsNotNone(bot._preload_memes_job)
        self.assertIsNotNone(bot._send_memes_job)
        self.assertIsNotNone(bot._clean_database_job)

        await bot.stopAsync()

    async def testInitMethods(self):
        bot = self._createSilentTelegramBot()
        self.assertIsInstance(bot, TelegramBot)

        set_memes_mock = MagicMock()
        startup_mock = MagicMock()
        bot._setMemesRoutineInterval = set_memes_mock
        bot._botStartupRoutine = startup_mock

        await bot.startAsync()
        await asyncio.sleep(3)
        await bot.stopAsync()

        set_memes_mock.assert_called_once()
        startup_mock.assert_called_once()

    async def testSendMemesRoutine(self):
        bot = self._createSilentTelegramBot()
        # get the seconds since midnight
        seconds_since_midnight = (
            datetime.now() - datetime.now().replace(hour=0, minute=0, second=0)
        ).total_seconds()
        # set the bot to send a meme an hour
        bot._settings["memes_per_day"] = 24
        # set the bot to send a meme in 30 seconds
        bot._settings["preload_time"] = 25
        bot._settings["start_delay"] = int(seconds_since_midnight + 30)

        # create a mock bot
        # start the bot
        await bot.startAsync()
        bot_mock = AsyncMock()
        send_message_mock = AsyncMock()
        send_media_mock = AsyncMock()

        bot_mock.send_message = send_message_mock
        bot_mock.send_video = send_media_mock
        bot_mock.send_photo = send_media_mock
        bot._application.bot = bot_mock

        # wait for the bot to send the memes
        await asyncio.sleep(35)

        # stop the bot
        await bot.stopAsync()

        # check if the bot sent the memes
        call_count = send_message_mock.call_count + send_media_mock.call_count
        self.assertEqual(call_count, 1)

    async def testCleanRoutine(self):
        bot = TelegramBot()
        # get the seconds since midnight
        seconds_since_midnight = (
            datetime.now() - datetime.now().replace(hour=0, minute=0, second=0)
        ).total_seconds()
        # set the bot to clean the database 5 seconds from now
        bot._settings["clean_database_time"] = int(seconds_since_midnight + 5)

    async def testCommands(self):
        ...
