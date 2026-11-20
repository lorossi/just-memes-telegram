"""Helpers shared by the tests."""

import shutil
import tempfile
from pathlib import Path

import ujson
from PIL import Image, ImageDraw, ImageFont

DATA_FOLDER = Path(__file__).parent / "data"
TEST_SETTINGS_PATH = DATA_FOLDER / "settings.json"
REAL_SETTINGS_PATH = Path("settings/settings.json")


def notNone[T](value: T | None) -> T:
    """Check that a value is not None, narrowing its type.

    Args:
        value (T | None): the value to check

    Returns:
        T: the value

    Raises:
        AssertionError: if the value is None
    """
    if value is None:
        msg = "Unexpected None value."
        raise AssertionError(msg)
    return value


class TempSettingsMixin:
    """Create a temporary folder and a settings file whose paths point inside it."""

    temp_dir: Path
    settings_path: str

    def createTempSettings(self) -> None:
        """Create the temporary folder and the settings file."""
        self.temp_dir = Path(tempfile.mkdtemp())
        settings = ujson.loads(TEST_SETTINGS_PATH.read_text(encoding="utf-8"))
        settings["VideoDownloader"]["temp_folder"] = str(self.temp_dir / "tmp")
        settings["Database"]["database_path"] = str(self.temp_dir / "test.db")

        settings_path = self.temp_dir / "settings.json"
        settings_path.write_text(ujson.dumps(settings), encoding="utf-8")
        self.settings_path = str(settings_path)

    def removeTempSettings(self) -> None:
        """Remove the temporary folder and all its content."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def createTextImage(path: Path, text: str, size: tuple[int, int] = (800, 200)) -> Path:
    """Create a png image with black text on a white background.

    Args:
        path (Path): where to save the image
        text (str): the text to draw
        size (tuple[int, int], optional): size of the image

    Returns:
        Path: the path of the image
    """
    with Image.new("RGB", size, "white") as im:
        draw = ImageDraw.Draw(im)
        font = ImageFont.load_default(size=72)
        draw.text((40, 50), text, fill="black", font=font)
        im.save(path)
    return path
