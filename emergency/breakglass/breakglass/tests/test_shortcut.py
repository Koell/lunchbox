import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from breakglass import icon, shortcut
from breakglass.state import State


class IconTest(unittest.TestCase):
    def test_pixels_are_square(self):
        self.assertEqual({len(row) for row in icon.PIXELS}, {16})
        self.assertEqual(len(icon.PIXELS), 16)
        self.assertTrue(set("".join(icon.PIXELS)) <= set(icon.COLORS) | {"."})

    def test_ico_holds_16_32_and_48_pixel_images(self):
        data = icon.ico_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, kind, count), (0, 1, 3))
        sizes = []
        for n in range(count):
            width, height, _, _, _, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * n)
            self.assertEqual((width, bits), (height, 32))
            self.assertLessEqual(offset + length, len(data))
            sizes.append(width)
        self.assertEqual(sizes, [16, 32, 48])


class ShortcutTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcut")
    def test_windows_shortcut(self):
        with mock.patch.object(shortcut, "LOCAL", self.dir / "local"):
            link = shortcut.install(self.dir)
        self.assertEqual(link, self.dir / "Break glass.lnk")
        self.assertTrue(link.exists())
        self.assertTrue((self.dir / "local" / "breakglass.ico").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux launcher")
    def test_linux_launcher(self):
        desktop = self.dir / "Desktop"
        desktop.mkdir()
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.dir / "share")}):
            link = shortcut.install(desktop)
        text = link.read_text(encoding="utf-8")
        self.assertIn("Exec=", text)
        self.assertIn("-m breakglass", text)
        self.assertTrue((self.dir / "share" / "applications" / "breakglass.desktop").exists())


class StateTest(unittest.TestCase):
    def test_folder_is_remembered_and_a_damaged_file_means_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = State(Path(tmp) / "local")
            self.assertIsNone(state.folder())
            state.remember_folder(Path("C:/projects"))
            self.assertEqual(State(Path(tmp) / "local").folder(), Path("C:/projects"))
            state.path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(state.folder())
