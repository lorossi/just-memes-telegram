from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


from modules.database import Database
from modules.telegrambot import TelegramBot


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
    _commandRegexes = {
        "_botStartCommand": r"\*Welcome in the Just Memes backend bot\*\n"
        r"\*This bot is used to manage the Just Memes meme channel\*\n"
        r"_Join us at_ \@justmemes69lmao\w*",
        "_botPingCommand": r"🏓 \*PONG\* 🏓",
        "_botStatusCommand": r"([a-zA-Z]+:(\n\t·\s[a-zA-Z]+: \w+)+\n)+",
        "_botNextpostCommand": r"_The next meme is scheduled for:_ \d{4}-\d{2}-\d{2} "
        r"\d{2}:\d{2}:\d{2}\n"
        r"_The next preload is scheduled for:_ \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
        "_botQueueCommand": r"\*The queue is empty\*"
        r"\n_Pass the links as argument to set them_",
        "_botResetCommand": r"_Resetting\.\.\._",
        "_botStopCommand": r"_Bot stopped_",
        "error": r"\*This command is for admins only\*",
    }

    def setUp(self) -> None:
        d = Database()
        d.deleteAll()

    def tearDown(self) -> None:
        d = Database()
        d.deleteAll()

    def _createMockBot(self):
        bot = AsyncMock()
        return bot

    def _createMockContext(self):
        context = AsyncMock()
        return context

    def _createMockUpdate(self, chat_id: int = 123456789):
        update = AsyncMock()
        update.effective_chat.id = chat_id

        return update

    async def _createPatchedBot(self):
        bot = TelegramBot()

        bot_mock = self._createMockBot()

        # manually start the bot
        bot._setupApplication()
        await bot._application.initialize()
        await bot._updater.start_polling()
        await bot._application.start()
        # inject the mock bot
        bot._application.bot = bot_mock
        # stop the routines
        bot._preload_memes_job.schedule_removal()
        bot._send_memes_job.schedule_removal()
        bot._clean_database_job.schedule_removal()

        return bot, bot_mock

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
        bot_mock = self._createMockBot()
        bot._application.bot = bot_mock

        # wait for the bot to send the memes
        await asyncio.sleep(35)

        # stop the bot
        await bot.stopAsync()

        # check if the bot sent the memes
        call_count = bot_mock.send_video.call_count + bot_mock.send_photo.call_count
        self.assertEqual(call_count, 1)

    async def testCleanRoutine(self):
        bot = TelegramBot()
        # get the seconds since midnight
        seconds_since_midnight = (
            datetime.now() - datetime.now().replace(hour=0, minute=0, second=0)
        ).total_seconds()
        # set the bot to clean the database 5 seconds from now
        bot._settings["clean_database_time"] = int(seconds_since_midnight + 5)

    async def testCommandsAdmin(self):
        chat_id = 123456789

        update_mock = self._createMockUpdate(chat_id=chat_id)
        context_mock = self._createMockContext()

        # manually start the bot
        bot, bot_mock = await self._createPatchedBot()
        # set the admins
        bot._settings["admins"] = [chat_id]

        for command in [
            bot._botStartCommand,
            bot._botPingCommand,
            bot._botStatusCommand,
            bot._botQueueCommand,
        ]:
            regex = self._commandRegexes[command.__name__]
            await command(update_mock, context_mock)
            args = bot_mock.send_message.call_args[1]
            self.assertRegex(args["text"], regex)
            self.assertEqual(args["chat_id"], chat_id)
            bot_mock.reset_mock()

    async def testCommandsAdminResetStop(self):
        # patch os to avoid stopping the bot
        chat_id = 123456789
        update_mock = self._createMockUpdate(chat_id=chat_id)
        context_mock = self._createMockContext()

        bot, bot_mock = await self._createPatchedBot()
        # manually start the bot
        bot._setupApplication()
        await bot._application.initialize()
        await bot._updater.start_polling()
        await bot._application.start()
        # inject the mock bot
        bot._application.bot = bot_mock
        # stop the routines
        bot._preload_memes_job.schedule_removal()
        bot._send_memes_job.schedule_removal()
        bot._clean_database_job.schedule_removal()
        # set the admins
        bot._settings["admins"] = [chat_id]

        with patch("modules.telegrambot.os") as os_mock:
            await bot._botResetCommand(update_mock, context_mock)
            args = bot_mock.send_message.call_args[1]
            self.assertRegex(args["text"], self._commandRegexes["_botResetCommand"])
            args = os_mock.execl.call_args[0]

            self.assertRegex(
                args[0],
                r"(/.*)+python",
            )
            os_mock.execl.assert_called_once()
            os_mock.reset_mock()

            await bot._botStopCommand(update_mock, context_mock)
            args = bot_mock.send_message.call_args[1]
            self.assertRegex(args["text"], self._commandRegexes["_botStopCommand"])
            os_mock._exit.assert_called_once()

    async def testCommandsNotAdmin(self):
        chat_id = 123456789
        admin_id = 987654321

        update_mock = self._createMockUpdate(chat_id=chat_id)
        context_mock = self._createMockContext()

        # manually start the bot
        bot, bot_mock = await self._createPatchedBot()
        # set the admins
        bot._settings["admins"] = [admin_id]

        for command in [
            bot._botResetCommand,
            bot._botStopCommand,
            bot._botStatusCommand,
            bot._botQueueCommand,
            bot._botCleanqueueCommand,
            bot._botNextpostCommand,
        ]:
            regex = self._commandRegexes["error"]
            await command(update_mock, context_mock)
            args = bot_mock.send_message.call_args[1]
            self.assertRegex(args["text"], regex)
            self.assertEqual(args["chat_id"], chat_id)
            bot_mock.reset_mock()
