import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bento import archive, gpg
from bento.archive import Bento, BentoError

T0 = datetime(2026, 10, 23, 14, 15, 2, tzinfo=timezone.utc)
STAMP = '"captured": "2026-10-23T14:15:02Z"'
LABEL = f'{{"version": 1, {STAMP}}}'.encode()
needs_gpg = unittest.skipUnless(gpg.available(), "gpg not installed")


def tar_bytes(entries, compression=""):
    """A tar archive; `entries` maps names to bytes or to a prepared TarInfo."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:" + compression) as tar:
        for name, value in entries.items():
            if isinstance(value, tarfile.TarInfo):
                tar.addfile(value)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
    return buffer.getvalue()


class BentoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "export" / "bento.tar.gz"

    def raw(self, outer, path=None):
        """Write a bento by hand; `outer` maps outer entry names to bytes or TarInfo."""
        path = path or self.dir / "raw.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tar_bytes(outer, "gz"))
        return path

    def raw_files(self, inner, label=LABEL):
        """A plain bento holding `inner` as contents.tar."""
        outer = {"contents.tar": tar_bytes(inner)}
        if label is not None:
            outer = {"label.json": label, **outer}
        return self.raw(outer)

    # Layout and round trip

    def test_round_trip(self):
        files = {"claude/CLAUDE.md": b"# rules\r\n", "git/aliases": b"[alias]\n", "m/a/b.bin": b"\0"}
        self.assertIsNone(archive.write(self.path, Bento(files, T0)))
        loaded = archive.read(self.path)
        self.assertEqual(dict(loaded.files), files)
        self.assertEqual((loaded.captured, loaded.encrypted, loaded.warnings), (T0, False, ()))
        self.assertEqual(loaded.modules(), ["claude", "git", "m"])
        self.assertEqual(loaded.module("m"), {"a/b.bin": b"\0"})
        self.assertEqual(loaded.module("missing"), {})

    def test_module_prefix_does_not_match_longer_names(self):
        contents = Bento({"git/a": b"1", "github/b": b"2"}, T0)
        self.assertEqual(contents.module("git"), {"a": b"1"})

    def test_plain_layout_and_normalized_metadata(self):
        archive.write(self.path, Bento({"c/run.sh": b"#!/bin/sh\n", "c/x.md": b"x"}, T0))
        with tarfile.open(self.path, "r:gz") as outer:
            self.assertEqual(outer.getnames(), ["label.json", "contents.tar"])
            label = json.loads(outer.extractfile("label.json").read())
            inner_bytes = outer.extractfile("contents.tar").read()
        self.assertEqual(label, {"version": 1, "captured": "2026-10-23T14:15:02Z"})
        with tarfile.open(fileobj=io.BytesIO(inner_bytes), mode="r:") as inner:
            members = inner.getmembers()
        self.assertEqual([m.name for m in members], ["c/run.sh", "c/x.md"])
        self.assertEqual({m.name: m.mode for m in members}, {"c/run.sh": 0o755, "c/x.md": 0o644})
        for member in members:
            self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""))
            self.assertEqual(member.mtime, int(T0.timestamp()))

    def test_default_path_is_the_export_folder(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(archive.DEFAULT_PATH, root / "export" / "bento.tar.gz")

    # Refusals

    def test_missing_bento(self):
        with self.assertRaisesRegex(BentoError, "no bento at .*pass --bento or run capture first"):
            archive.read(self.path)

    def test_not_a_tar_gz(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"plain text")
        with self.assertRaisesRegex(BentoError, "not a readable .tar.gz"):
            archive.read(self.path)

    def test_outer_layer_holds_only_label_and_one_contents(self):
        cases = {
            "stray file": ({"label.json": LABEL, "contents.tar": tar_bytes({}), "x": b""}, "unexpected"),
            "files at top": ({"label.json": LABEL, "claude/CLAUDE.md": b"x"}, "unexpected"),
            "no contents": ({"label.json": LABEL}, "exactly one"),
            "both contents": (
                {"contents.tar": tar_bytes({}), "contents.tar.gpg": b"x"},
                "exactly one",
            ),
        }
        for name, (outer, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(BentoError, message):
                    archive.read(self.raw(outer))

    def test_contents_must_be_a_tar(self):
        with self.assertRaisesRegex(BentoError, "contents are not a readable tar"):
            archive.read(self.raw({"label.json": LABEL, "contents.tar": b"garbage" * 100}))

    def test_refuses_unsafe_entries(self):
        cases = ["/etc/passwd", "m/../../evil", "m/./a", "m//a", "../m/a", "toplevel.txt",
                 "bad name/a", "m/a\\b", "C:/x/y", "m/c:d"]
        for name in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(BentoError, "unsafe path"):
                    archive.read(self.raw_files({name: b"x"}))

    def test_refuses_links_and_directories(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
            with self.subTest(kind):
                info = tarfile.TarInfo("m/a")
                info.type = kind
                info.linkname = "/etc/passwd"
                with self.assertRaisesRegex(BentoError, "not a regular file"):
                    archive.read(self.raw_files({"m/a": info}))

    def test_refuses_links_in_the_outer_layer(self):
        info = tarfile.TarInfo("contents.tar")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        with self.assertRaisesRegex(BentoError, "not a regular file"):
            archive.read(self.raw({"label.json": LABEL, "contents.tar": info}))

    def test_refuses_duplicates(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for data in (b"1", b"2"):
                info = tarfile.TarInfo("m/a")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        with self.assertRaisesRegex(BentoError, "duplicate entry: m/a"):
            archive.read(self.raw({"label.json": LABEL, "contents.tar": buffer.getvalue()}))

    # Label

    def test_label_problems_are_warnings_and_keep_the_files(self):
        cases = {
            "missing": (None, "no label.json", None),
            "empty": (b"", "not valid JSON", None),
            "not_json": (b"{", "not valid JSON", None),
            "not_utf8": (b"\xff\xfe", "not valid JSON", None),
            "not_object": (b"[1]", "not a JSON object", None),
            "no_version": (f"{{{STAMP}}}".encode(), "has no version", T0),
            "older": (f'{{"version": 0, {STAMP}}}'.encode(), "unknown version 0", T0),
            "not_int": (f'{{"version": "1", {STAMP}}}'.encode(), "unknown version '1'", T0),
            "bool": (f'{{"version": true, {STAMP}}}'.encode(), "unknown version True", T0),
            "bad_time": (b'{"version": 1, "captured": "yesterday"}', "no valid capture time", None),
            "time_not_string": (b'{"version": 1, "captured": 5}', "no valid capture time", None),
            "claims_encryption": (
                f'{{"version": 1, {STAMP}, "encryption": "gpg"}}'.encode(),
                "says encryption 'gpg', but the contents are plain",
                T0,
            ),
        }
        for name, (label, warning, captured) in cases.items():
            with self.subTest(name):
                loaded = archive.read(self.raw_files({"m/a": b"x"}, label))
                self.assertEqual(dict(loaded.files), {"m/a": b"x"})
                self.assertEqual(loaded.captured, captured)
                self.assertEqual(len(loaded.warnings), 1, loaded.warnings)
                self.assertIn(warning, loaded.warnings[0])

    def test_extra_label_fields_are_ignored(self):
        label = f'{{"version": 1, {STAMP}, "host": "x"}}'.encode()
        loaded = archive.read(self.raw_files({}, label))
        self.assertEqual((loaded.captured, loaded.warnings), (T0, ()))

    def test_newer_version_is_refused(self):
        label = f'{{"version": 2, {STAMP}}}'.encode()
        with self.assertRaisesRegex(BentoError, "bento version 2 is newer than this bento tool supports"):
            archive.read(self.raw_files({"m/a": b"x"}, label))

    # Backups

    def test_replacing_keeps_the_old_bento_named_after_its_capture_time(self):
        archive.write(self.path, Bento({"m/a": b"1"}, T0))
        first = archive.read(self.path)
        backup = archive.write(self.path, Bento({"m/a": b"2"}, T0 + timedelta(hours=1)), first)
        self.assertEqual(backup, self.path.with_name("bento.20261023T141502Z.tar.gz"))
        self.assertEqual(archive.read(backup).module("m"), {"a": b"1"})
        self.assertEqual(archive.read(self.path).module("m"), {"a": b"2"})
        self.assertEqual(archive.backups(self.path), [backup])

    def test_keeps_only_the_newest_backups(self):
        previous = None
        for hour in range(archive.KEEP_BACKUPS + 3):
            contents = Bento({"m/a": str(hour).encode()}, T0 + timedelta(hours=hour))
            archive.write(self.path, contents, previous)
            previous = contents
        kept = archive.backups(self.path)
        self.assertEqual(len(kept), archive.KEEP_BACKUPS)
        self.assertEqual(kept[0].name, "bento.20261023T201502Z.tar.gz")
        self.assertEqual(archive.read(self.path).module("m"), {"a": b"7"})

    def test_leaves_no_temporary_files(self):
        archive.write(self.path, Bento({"m/a": b"1"}, T0))
        archive.write(self.path, Bento({"m/a": b"2"}, T0 + timedelta(hours=1)), archive.read(self.path))
        names = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(names, ["bento.20261023T141502Z.tar.gz", "bento.tar.gz"])

    def test_failed_write_keeps_the_old_bento(self):
        archive.write(self.path, Bento({"m/a": b"1"}, T0))
        with self.assertRaises(TypeError):
            archive.write(self.path, Bento({"m/a": "not bytes"}, T0 + timedelta(hours=1)))
        self.assertEqual(archive.read(self.path).module("m"), {"a": b"1"})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["bento.tar.gz"])

    def test_backup_names_for_other_suffixes(self):
        other = self.dir / "mine.tgz"
        archive.write(other, Bento({"m/a": b"1"}, T0))
        backup = archive.write(other, Bento({"m/a": b"2"}, T0 + timedelta(hours=1)), archive.read(other))
        self.assertEqual(backup.name, "mine.20261023T141502Z.tgz")

    def test_backup_of_a_bento_without_capture_time_uses_the_file_time(self):
        self.raw({"contents.tar": tar_bytes({"m/a": b"1"})}, self.path)
        backup = archive.write(self.path, Bento({"m/a": b"2"}, T0), archive.read(self.path))
        self.assertEqual(archive.read(backup).module("m"), {"a": b"1"})
        self.assertRegex(backup.name, r"^bento\.\d{8}T\d{6}Z\.tar\.gz$")

    def test_write_insists_on_a_passphrase_exactly_when_encrypted(self):
        with self.assertRaises(ValueError):
            archive.write(self.path, Bento({}, T0, encrypted=True))
        with self.assertRaises(ValueError):
            archive.write(self.path, Bento({}, T0), passphrase="secret")
        self.assertFalse(self.path.exists())


@needs_gpg
class EncryptedBentoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "bento.tar.gz"
        self.files = {"ssh/id_ed25519": b"-----BEGIN OPENSSH PRIVATE KEY-----\n", "git/aliases": b"x"}

    def test_round_trip(self):
        archive.write(self.path, Bento(self.files, T0, encrypted=True), passphrase="correct horse")
        asked = []
        loaded = archive.read(self.path, lambda: asked.append(1) or "correct horse")
        self.assertEqual(dict(loaded.files), self.files)
        self.assertEqual((loaded.encrypted, loaded.captured, loaded.warnings), (True, T0, ()))
        self.assertEqual(asked, [1])

    def test_outer_layer_shows_nothing_but_the_label(self):
        archive.write(self.path, Bento(self.files, T0, encrypted=True), passphrase="correct horse")
        with tarfile.open(self.path, "r:gz") as outer:
            self.assertEqual(outer.getnames(), ["label.json", "contents.tar.gpg"])
            label = json.loads(outer.extractfile("label.json").read())
            sealed = outer.extractfile("contents.tar.gpg").read()
        self.assertEqual(label, {"version": 1, "captured": "2026-10-23T14:15:02Z", "encryption": "gpg"})
        self.assertNotIn(b"ssh/id_ed25519", sealed)
        self.assertNotIn(b"PRIVATE KEY", sealed)
        self.assertTrue(archive.is_encrypted(self.path))

    def test_plain_bento_never_asks(self):
        archive.write(self.path, Bento(self.files, T0))
        self.assertFalse(archive.read(self.path, lambda: self.fail("asked")).encrypted)
        self.assertFalse(archive.is_encrypted(self.path))

    def test_wrong_passphrase(self):
        archive.write(self.path, Bento(self.files, T0, encrypted=True), passphrase="correct horse")
        with self.assertRaisesRegex(BentoError, "wrong passphrase, or the bento is damaged"):
            archive.read(self.path, lambda: "battery staple")

    def test_encrypted_bento_without_a_way_to_ask(self):
        archive.write(self.path, Bento(self.files, T0, encrypted=True), passphrase="correct horse")
        with self.assertRaisesRegex(BentoError, "the bento is encrypted"):
            archive.read(self.path)

    def test_tampered_contents_are_refused(self):
        archive.write(self.path, Bento(self.files, T0, encrypted=True), passphrase="correct horse")
        with tarfile.open(self.path, "r:gz") as outer:
            label = outer.extractfile("label.json").read()
            sealed = bytearray(outer.extractfile("contents.tar.gpg").read())
        sealed[-30] ^= 1
        self.path.write_bytes(tar_bytes({"label.json": label, "contents.tar.gpg": bytes(sealed)}, "gz"))
        with self.assertRaisesRegex(BentoError, "wrong passphrase, or the bento is damaged"):
            archive.read(self.path, lambda: "correct horse")

    def test_drop_plain_backups_keeps_encrypted_ones(self):
        archive.write(self.path, Bento({"m/a": b"1"}, T0))
        plain = archive.read(self.path)
        archive.write(self.path, Bento({"m/a": b"2"}, T0 + timedelta(hours=1), True), plain,
                    passphrase="pw12")
        sealed = archive.read(self.path, lambda: "pw12")
        archive.write(self.path, Bento({"m/a": b"3"}, T0 + timedelta(hours=2), True), sealed,
                    passphrase="pw12")
        self.assertEqual(len(archive.backups(self.path)), 2)
        dropped = archive.drop_plain_backups(self.path)
        self.assertEqual([p.name for p in dropped], ["bento.20261023T141502Z.tar.gz"])
        self.assertEqual([p.name for p in archive.backups(self.path)], ["bento.20261023T151502Z.tar.gz"])

    def test_passphrase_with_line_break_is_refused(self):
        with self.assertRaisesRegex(BentoError, "line breaks"):
            archive.write(self.path, Bento({}, T0, encrypted=True), passphrase="a\nb")
        self.assertFalse(self.path.exists())

    def test_meta_entries_are_encrypted_with_the_files(self):
        meta = {"selection.json": b'{"exclude": ["skills/client-x/"]}'}
        archive.write(self.path, Bento(self.files, T0, True, meta=meta), passphrase="pw12")
        with tarfile.open(self.path, "r:gz") as outer:
            self.assertEqual(outer.getnames(), ["label.json", "contents.tar.gpg"])
            self.assertNotIn(b"client-x", outer.extractfile("label.json").read())
        self.assertEqual(dict(archive.read(self.path, lambda: "pw12").meta), meta)


class MetaTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "bento.tar.gz"

    def test_meta_round_trips_under_the_bento_folder(self):
        meta = {"selection.json": b"{}\n"}
        archive.write(self.path, Bento({"m/a": b"1"}, T0, meta=meta))
        loaded = archive.read(self.path)
        self.assertEqual((dict(loaded.files), dict(loaded.meta)), ({"m/a": b"1"}, meta))
        self.assertEqual(loaded.modules(), ["m"])
        with tarfile.open(self.path, "r:gz") as outer:
            inner = tarfile.open(fileobj=io.BytesIO(outer.extractfile("contents.tar").read()))
            self.assertEqual(sorted(inner.getnames()), [".bento/selection.json", "m/a"])

    def test_a_bento_without_meta_reads_empty_meta(self):
        archive.write(self.path, Bento({"m/a": b"1"}, T0))
        self.assertEqual(dict(archive.read(self.path).meta), {})

    def test_unsafe_names_under_the_bento_folder_are_refused(self):
        for name in (".bento/sub/x.json", ".bento/../m/a", ".bento/", ".other/x.json", ".bento/.x"):
            with self.subTest(name):
                outer = {"label.json": LABEL, "contents.tar": tar_bytes({name: b"x"})}
                self.path.write_bytes(tar_bytes(outer, "gz"))
                with self.assertRaisesRegex(BentoError, "unsafe path"):
                    archive.read(self.path)


if __name__ == "__main__":
    unittest.main()
