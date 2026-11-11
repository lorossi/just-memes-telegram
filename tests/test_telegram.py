"""Unit tests for the telegram bot module."""

import asyncio
import unittest
from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from imagehash import hex_to_hash

from just_memes_telegram.modules.data import Fingerprint, Post
from just_memes_telegram.modules.entities import DownloadResult
from just_memes_telegram.modules.telegrambot import ADMIN_ONLY_MESSAGE, TelegramBot
from tests.utils import REAL_SETTINGS_PATH, TempSettingsMixin, createTextImage

ADMIN_ID = 123456789
USER_ID = 987654321


class TelegramBotTest(TempSettingsMixin, unittest.IsolatedAsyncioTestCase):
    """Unit tests for the TelegramBot class, with a mocked Telegram bot."""

    COMMAND_REGEXES: ClassVar[dict[str, str]] = {
        "_botStartCommand": r"\*Welcome in the Just Memes backend bot\*\n"
        r"\*This bot is used to manage the Just Memes meme channel\*\n"
        r"_Join us at_ @justmemes69lmao",
        "_botPingCommand": r"🏓 \*PONG\* 🏓",
        "_botStatusCommand": r"^TelegramBot:(\n\t· .+)+\n\nReddit:",
        "_botNextpostCommand": r"_The next meme is scheduled for:_ not scheduled\n"
        r"_The next preload is scheduled for:_ not scheduled",
        "_botQueueCommand": r"\*The queue is empty\*"
        r"\n_Pass the links as argument to set them_",
        "_botCleanqueueCommand": r"Queue cleaned",
        "_botResetCommand": r"_Resetting\.\.\._",
        "_botStopCommand": r"_Bot stopped_",
    }

    async def asyncSetUp(self) -> None:
        """Create the bot and connect its database."""
        self.createTempSettings()
        self.bot = TelegramBot(settings_path=self.settings_path)
        await self.bot._database.connect()

    async def asyncTearDown(self) -> None:
        """Release the resources and remove the temporary folder."""
        await self.bot.stop()
        self.removeTempSettings()

    @staticmethod
    def _createMockUpdate(chat_id: int) -> MagicMock:
        """Create an update coming from a chat.

        Args:
            chat_id (int): id of the chat

        Returns:
            MagicMock: the update
        """
        update = MagicMock()
        update.effective_chat.id = chat_id
        return update

    @staticmethod
    def _createMockContext(args: list[str] | None = None) -> MagicMock:
        """Create a context with a mocked bot.

        Args:
            args (list[str], optional): arguments of the command

        Returns:
            MagicMock: the context
        """
        context = MagicMock()
        context.bot = AsyncMock()
        context.args = args or []
        return context

    def _createPost(self, name: str, title: str = "a meme") -> Post:
        """Create a post whose media is a local image.

        Args:
            name (str): name of the post (used as id and file name)
            title (str, optional): title of the post

        Returns:
            Post: the post
        """
        path = createTextImage(self.bot._downloader.temp_folder / f"{name}.png", name)
        return Post(
            url=f"https://i.redd.it/{name}.png", id=name, title=title, path=str(path)
        )

    def _mockDownloads(self, hash_str: str = "ffff0000ffff0000") -> None:
        """Mock the download and the fingerprint of the media.

        Args:
            hash_str (str, optional): hash returned by the fingerprinter
        """

        def download(url: str) -> DownloadResult:
            name = url.rsplit("/", 1)[-1].removesuffix(".png")
            path = createTextImage(
                self.bot._downloader.temp_folder / f"{name}.png", name
            )
            return DownloadResult(
                status=HTTPStatus.OK, path=str(path), preview_path=str(path)
            )

        def fingerprint(img_url: str, **_: object) -> Fingerprint:
            return Fingerprint(
                caption="a meme",
                hash=hex_to_hash(hash_str),
                hash_str=hash_str,
                url=img_url,
            )

        self.bot._downloader.downloadMedia = AsyncMock(side_effect=download)
        self.bot._fingerprinter.fingerprint = AsyncMock(side_effect=fingerprint)

    async def testProperties(self) -> None:
        """Test the properties and the string representation."""
        self.assertListEqual(self.bot.words_to_skip, ["reddit", "upvote"])
        self.assertEqual(self.bot.words_to_skip_str, "reddit, upvote")
        self.assertFalse(self.bot.restart_requested)
        self.assertEqual(repr(self.bot), str(self.bot))
        self.assertIn("start delay: 0 seconds", str(self.bot))

    async def testTextValid(self) -> None:
        """Test that the blocked words are matched case insensitively."""
        self.assertTrue(self.bot._textValid(None))
        self.assertTrue(self.bot._textValid(""))
        self.assertTrue(self.bot._textValid("a normal meme"))
        self.assertFalse(self.bot._textValid("please UPVOTE this"))
        self.assertFalse(self.bot._textValid("reddit moment"))

        # empty words would match every text
        self.bot._settings["words_to_skip"] = [""]
        self.assertTrue(self.bot._textValid("a normal meme"))

    async def testScheduling(self) -> None:
        """Test the calculation of the next preload and post."""
        tz = ZoneInfo("Europe/Rome")
        seconds_between = 3600
        preload_time = 30
        self.bot._settings.update(
            posts_per_day=24, preload_time=preload_time, start_delay=600
        )
        self.assertEqual(self.bot._getSecondsBetweenPosts(), seconds_between)

        for now in [
            datetime(2026, 11, 11, 0, 0, 0, tzinfo=tz),
            datetime(2026, 11, 11, 0, 9, 30, tzinfo=tz),
            datetime(2026, 11, 11, 13, 45, 12, tzinfo=tz),
            datetime(2026, 11, 11, 23, 59, 59, tzinfo=tz),
        ]:
            # brute force the expected value
            expected = now.replace(hour=0, minute=10, second=0) - timedelta(
                seconds=preload_time
            )
            while expected <= now:
                expected += timedelta(seconds=seconds_between)

            seconds, timestamp = self.bot._calculatePreload(now)
            self.assertEqual(seconds, (expected - now).total_seconds())
            self.assertEqual(timestamp, expected.isoformat(sep=" "))

            seconds, timestamp = self.bot._calculateNextPost(now)
            expected_post = expected + timedelta(seconds=preload_time)
            self.assertEqual(seconds, (expected_post - now).total_seconds())
            self.assertEqual(timestamp, expected_post.isoformat(sep=" "))

    async def testCommandsAdmin(self) -> None:
        """Test the replies of the commands to an admin."""
        update = self._createMockUpdate(ADMIN_ID)
        for name, regex in self.COMMAND_REGEXES.items():
            context = self._createMockContext()
            await getattr(self.bot, name)(update, context)
            kwargs = context.bot.send_message.call_args.kwargs
            self.assertRegex(kwargs["text"], regex, name)
            self.assertEqual(kwargs["chat_id"], ADMIN_ID)

    async def testCommandsNotAdmin(self) -> None:
        """Test that admin commands are refused to other users."""
        update = self._createMockUpdate(USER_ID)
        for name in [
            "_botResetCommand",
            "_botStopCommand",
            "_botStatusCommand",
            "_botQueueCommand",
            "_botCleanqueueCommand",
            "_botNextpostCommand",
        ]:
            context = self._createMockContext()
            await getattr(self.bot, name)(update, context)
            context.bot.send_message.assert_called_once_with(
                chat_id=USER_ID, text=ADMIN_ONLY_MESSAGE
            )

        self.assertFalse(self.bot._stop_event.is_set())

    async def testStopRequests(self) -> None:
        """Test that the stop and reset commands request a stop."""
        update = self._createMockUpdate(ADMIN_ID)
        await self.bot._botStopCommand(update, self._createMockContext())
        await asyncio.wait_for(self.bot.waitForStopRequest(), timeout=1)
        self.assertFalse(self.bot.restart_requested)

        await self.bot._botResetCommand(update, self._createMockContext())
        self.assertTrue(self.bot.restart_requested)

    async def testQueueCommand(self) -> None:
        """Test adding urls to the queue and cleaning it."""
        self._mockDownloads()
        update = self._createMockUpdate(ADMIN_ID)
        urls = ["https://i.redd.it/first.png", "https://i.redd.it/second.png"]

        context = self._createMockContext(urls)
        await self.bot._botQueueCommand(update, context)
        self.assertRegex(
            context.bot.send_message.call_args.kwargs["text"], r"^2 _Images added"
        )
        self.assertListEqual([p.url for p in self.bot._queue], urls)
        self.assertTupleEqual(await self.bot._database.getStoredData(), (2, 2))

        context = self._createMockContext()
        await self.bot._botQueueCommand(update, context)
        self.assertIn("first", context.bot.send_message.call_args.kwargs["text"])

        paths = [Path(p.path or "") for p in self.bot._queue]
        await self.bot._botCleanqueueCommand(update, self._createMockContext())
        self.assertListEqual(self.bot._queue, [])
        self.assertFalse(any(p.exists() for p in paths))

    async def testSendRoutine(self) -> None:
        """Test that the first post of the queue is sent and its file deleted."""
        context = self._createMockContext()
        await self.bot._botSendmemeRoutine(context)
        context.bot.send_photo.assert_not_called()

        post = self._createPost("first")
        self.bot._queue.append(post)
        await self.bot._botSendmemeRoutine(context)

        context.bot.send_photo.assert_called_once_with(
            chat_id="@justmemes69lmao",
            photo=Path(post.path or ""),
            caption="test caption",
        )
        self.assertListEqual(self.bot._queue, [])
        self.assertFalse(Path(post.path or "").exists())

        video = self._createPost("video")
        video.video = True
        self.bot._queue.append(video)
        await self.bot._botSendmemeRoutine(context)
        context.bot.send_video.assert_called_once()

    async def testPreloadRoutine(self) -> None:
        """Test that old, reposted and blocked posts are skipped."""
        repost_hash = "ffff0000ffff0000"
        self._mockDownloads(hash_str=repost_hash)
        await self.bot._database.addData(
            post=Post(url="https://i.redd.it/old.png", id="old"),
            fingerprint=Fingerprint(
                caption=None,
                hash=hex_to_hash(repost_hash),
                hash_str=repost_hash,
                url="https://i.redd.it/old.png",
            ),
        )

        posts = [
            Post(url="https://i.redd.it/old.png", id="old", title="old"),
            Post(url="https://i.redd.it/blocked.png", id="blocked", title="Upvote!"),
            Post(url="https://i.redd.it/repost.png", id="repost", title="repost"),
        ]
        self.bot._reddit.fetch = AsyncMock(return_value=posts)
        await self.bot._botPreloadmemeRoutine(self._createMockContext())
        self.assertListEqual(self.bot._queue, [])
        # the repost file is deleted
        self.assertListEqual(list(self.bot._downloader.temp_folder.iterdir()), [])
        self.assertSetEqual(
            await self.bot._database.getOldIds(), {"old", "blocked", "repost"}
        )

        self._mockDownloads(hash_str="0123456789abcdef")
        valid = Post(url="https://i.redd.it/valid.png", id="valid", title="valid")
        self.bot._reddit.fetch = AsyncMock(return_value=[*posts, valid])
        await self.bot._botPreloadmemeRoutine(self._createMockContext())
        self.assertListEqual(self.bot._queue, [valid])
        self.assertTrue(Path(valid.path or "").exists())

        # the queue is full, nothing is fetched
        self.bot._reddit.fetch.reset_mock()
        await self.bot._botPreloadmemeRoutine(self._createMockContext())
        self.bot._reddit.fetch.assert_not_called()

    async def testCleanRoutineKeepsQueue(self) -> None:
        """Test that the clean routine does not delete the queued files."""
        queued = self._createPost("queued")
        self.bot._queue.append(queued)
        createTextImage(self.bot._downloader.temp_folder / "stale.png", "stale")

        await self.bot._botCleanRoutine(self._createMockContext())
        self.assertListEqual(
            list(self.bot._downloader.temp_folder.iterdir()), [Path(queued.path or "")]
        )

    async def testErrorHandler(self) -> None:
        """Test that errors are sent, escaped and truncated, to the admins."""
        context = self._createMockContext()
        context.error = ValueError("_bad_ " * 2000)

        await self.bot._botError(None, context)
        self.assertEqual(context.bot.send_message.call_count, 4)
        for call in context.bot.send_message.call_args_list:
            self.assertEqual(call.kwargs["chat_id"], ADMIN_ID)
            self.assertLessEqual(len(call.kwargs["text"]), 4096)
            self.assertNotRegex(call.kwargs["text"], r"(?<!\\)_")

    @pytest.mark.credentials
    async def testStartStop(self) -> None:
        """Test starting and stopping the bot with real credentials."""
        await self.bot.stop()
        bot = TelegramBot(settings_path=REAL_SETTINGS_PATH)
        bot._settings["admins"] = []
        await bot.start()
        self.assertNotEqual(bot._getNextTimestamps()[0], "not scheduled")
        await bot.stop()
