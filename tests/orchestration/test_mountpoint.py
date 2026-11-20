import os
import tempfile
import unittest
from pathlib import Path

from vfspy.orchestration.mountpoint import Mountpoint


class MountPointValidationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "mount"

    def test_accepts_absolute_string_and_path_without_creating_directory(self):
        for value in (str(self.path), self.path):
            with self.subTest(value=value):
                Mountpoint(value)
                self.assertFalse(self.path.exists())

    def test_rejects_relative_paths(self):
        for value in ("", ".", "mount", "parent/mount"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Mountpoint(value)

    def test_rejects_nul_character(self):
        with self.assertRaises(ValueError):
            Mountpoint(str(self.path) + "\x00")

    def test_rejects_missing_parent(self):
        with self.assertRaises(ValueError):
            Mountpoint(self.path / "child")

    def test_rejects_parent_that_is_a_file(self):
        self.path.touch()
        with self.assertRaises(ValueError):
            Mountpoint(self.path / "child")

    def test_accepts_normal_directory_names(self):
        for name in ("mount", "mount point", ".hidden", "mount.txt", "COM10", "console", "挂载点"):
            with self.subTest(name=name):
                path = self.path.with_name(name)
                Mountpoint(path)
                self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows filename rules")
    def test_windows_rejects_reserved_directory_names(self):
        for name in (
            "CON",
            "con.txt",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "LPT9",
            "mount.",
            "mount ",
            "bad<name",
            "bad>name",
            "bad:name",
            'bad"name',
            "bad|name",
            "bad?name",
            "bad*name",
            "bad\x1fname",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Mountpoint(self.path.with_name(name))

    @unittest.skipIf(os.name == "nt", "POSIX filename rules")
    def test_non_windows_does_not_apply_windows_name_restrictions(self):
        for name in ("CON", "nul.txt", "COM1", "mount.", "mount ", "mount?name", "mount:name"):
            with self.subTest(name=name):
                Mountpoint(self.path.with_name(name))


class MountPointLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "mount"
        self.mount = Mountpoint(self.path)

    @unittest.skipUnless(os.name == "nt", "WinFsp mountpoint lifecycle")
    async def test_windows_prepare_leaves_directory_creation_to_winfsp(self):
        await self.mount.prepare()
        self.assertFalse(self.path.exists())
        # WinFsp owns the directory even if it appears after prepare().
        self.path.mkdir()
        await self.mount.close()
        self.assertTrue(self.path.is_dir())

    @unittest.skipUnless(os.name == "nt", "WinFsp mountpoint lifecycle")
    async def test_windows_prepare_rejects_existing_directory_or_file(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                if directory:
                    self.path.mkdir()
                else:
                    self.path.touch()
                with self.assertRaises(RuntimeError):
                    await self.mount.prepare()
                await self.mount.close()
                self.assertTrue(self.path.exists())
                if directory:
                    self.path.rmdir()
                else:
                    self.path.unlink()

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_posix_prepare_creates_directory_and_close_removes_it(self):
        await self.mount.prepare()
        self.assertTrue(self.path.is_dir())
        await self.mount.close()
        self.assertFalse(self.path.exists())

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_posix_prepare_rejects_existing_directory_or_file(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                if directory:
                    self.path.mkdir()
                else:
                    self.path.touch()
                with self.assertRaises(FileExistsError):
                    await self.mount.prepare()
                await self.mount.close()
                self.assertTrue(self.path.exists())
                if directory:
                    self.path.rmdir()
                else:
                    self.path.unlink()

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_failed_prepare_does_not_claim_external_directory(self):
        self.path.parent.rmdir()
        with self.assertRaises(OSError):
            await self.mount.prepare()
        self.path.parent.mkdir()
        self.path.mkdir()
        await self.mount.close()
        self.assertTrue(self.path.is_dir())

    async def test_close_before_prepare_preserves_existing_directory(self):
        self.path.mkdir()
        await self.mount.close()
        await self.mount.close()
        self.assertTrue(self.path.is_dir())

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_repeated_close_preserves_new_directory_at_same_path(self):
        await self.mount.prepare()
        await self.mount.close()
        self.path.mkdir()
        await self.mount.close()
        self.assertTrue(self.path.is_dir())

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_close_tolerates_directory_already_removed(self):
        await self.mount.prepare()
        self.path.rmdir()
        await self.mount.close()
        self.assertFalse(self.path.exists())
        self.path.mkdir()
        await self.mount.close()
        self.assertTrue(self.path.is_dir())

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_close_preserves_replacement_directory_or_file(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                await self.mount.prepare()
                # Keep the original inode alive so filesystem inode reuse cannot affect the test.
                original = self.path.with_name("original")
                self.path.rename(original)
                if directory:
                    self.path.mkdir()
                else:
                    self.path.touch()
                await self.mount.close()
                self.assertTrue(self.path.exists())
                self.assertEqual(self.path.is_dir(), directory)
                self.assertTrue(original.is_dir())
                if directory:
                    self.path.rmdir()
                else:
                    self.path.unlink()
                # Once close() has completed, the original directory is no longer owned.
                original.rename(self.path)
                await self.mount.close()
                self.assertTrue(self.path.is_dir())
                self.path.rmdir()

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    async def test_close_preserves_symlink_replacing_directory(self):
        await self.mount.prepare()
        original = self.path.with_name("original")
        self.path.rename(original)
        self.path.symlink_to(original, target_is_directory=True)
        await self.mount.close()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(self.path.resolve(), original)
        self.assertTrue(original.is_dir())

    @unittest.skipIf(os.name == "nt", "FUSE mountpoint lifecycle")
    async def test_close_preserves_nonempty_directory_and_can_be_retried(self):
        await self.mount.prepare()
        child = self.path / "data.txt"
        child.write_text("keep me", encoding="utf-8")
        with self.assertRaises(OSError):
            await self.mount.close()
        self.assertEqual(child.read_text(encoding="utf-8"), "keep me")
        child.unlink()
        await self.mount.close()
        self.assertFalse(self.path.exists())
