import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from vfspy.process import BridgeProcess, BridgeProcessConfig


class BridgeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.code = "pass"
        self.calls = []
        self.processes = []
        self.children = []
        self.spawn_entered = asyncio.Event()
        self.spawn_release = asyncio.Event()
        self.spawn_release.set()
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            self.spawn_entered.set()
            await self.spawn_release.wait()
            process = await real_spawn(sys.executable, "-c", self.code, *args[1:], **kwargs)
            self.children.append(process)
            return process

        self.spawn_patch = patch(
            "vfspy.process.manager.asyncio.create_subprocess_exec", side_effect=spawn
        )
        self.spawn_patch.start()

    async def asyncTearDown(self):
        self.spawn_release.set()
        try:
            for process in self.processes:
                await process.close()
        finally:
            self.spawn_patch.stop()

    def process(self, **kwargs):
        process = BridgeProcess(
            config(**{"executable": sys.executable, "stop_timeout": 0.1, **kwargs})
        )
        self.processes.append(process)
        return process

    async def test_arguments_and_environment(self):
        mountpoint = "mount dir & $(literal)"
        process = self.process(mountpoint=mountpoint, max_message_bytes=2048, startup_timeout=0.125)
        with patch.dict(os.environ, {"VFSPY_TOKEN": "parent-secret"}):
            self.assertIs(await process.start(), process)
            self.assertEqual(os.environ["VFSPY_TOKEN"], "parent-secret")
        expected = [
            "--endpoint",
            "127.0.0.1:12345",
            "--mountpoint",
            mountpoint,
            "--max-message-bytes",
            "2048",
            "--startup-timeout",
            "125ms",
        ]
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, (str(Path(sys.executable).resolve()), *expected))
        self.assertEqual(kwargs["env"]["VFSPY_TOKEN"], "child-secret")
        self.assertNotIn("child-secret", argv)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIsNone(kwargs.get("stderr"))
        self.assertEqual(
            kwargs["creationflags"], subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        with self.assertRaises(RuntimeError):
            await process.start()

    async def test_close_after_child_exits(self):
        self.code = "raise SystemExit(7)"
        process = await self.process().start()
        child = self.children[0]
        self.assertEqual(await asyncio.wait_for(child.wait(), 5), 7)
        with patch.object(child, "terminate") as terminate:
            await process.close()
        terminate.assert_not_called()
        with self.assertRaises(RuntimeError):
            await process.start()

    async def test_context_exit_reaps_process_and_preserves_body_error(self):
        self.code = "import time; time.sleep(60)"
        process = self.process()
        with self.assertRaisesRegex(ValueError, "body failure"):
            async with process as value:
                self.assertIs(value, process)
                self.assertIsNone(self.children[0].returncode)
                raise ValueError("body failure")
        self.assertIsNotNone(self.children[0].returncode)
        await asyncio.gather(process.close(), process.close())

    async def test_close_during_start_waits_and_reaps_child(self):
        self.code = "import time; time.sleep(60)"
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        closing = asyncio.create_task(process.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.spawn_release.set()
        self.assertIs(await starting, process)
        await closing
        self.assertIsNotNone(self.children[0].returncode)

    async def test_cancelled_start_releases_lifecycle_lock(self):
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        await process.close()
        self.assertEqual(self.children, [])

    async def test_cancelled_close_kills_and_reaps_child(self):
        self.code = "import time; time.sleep(60)"
        process = await self.process(stop_timeout=5).start()
        child = self.children[0]
        entered = asyncio.Event()
        real_wait = child.wait

        async def delayed_wait():
            if not entered.is_set():
                entered.set()
                await asyncio.Future()
            return await asyncio.wait_for(real_wait(), 5)

        with (
            patch.object(child, "terminate"),
            patch.object(child, "wait", side_effect=delayed_wait),
        ):
            closing = asyncio.create_task(process.close())
            await entered.wait()
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await closing
        self.assertIsNotNone(child.returncode)

    @unittest.skipIf(os.name == "nt", "Windows terminate is already forceful")
    async def test_close_escalates_to_kill_when_sigterm_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / "ready"
            self.code = (
                "import signal,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"Path({str(ready)!r}).touch(); time.sleep(60)"
            )
            process = await self.process().start()
            async with asyncio.timeout(5):
                while not ready.exists():
                    await asyncio.sleep(0.01)
            await process.close()
        self.assertEqual(self.children[0].returncode, -signal.SIGKILL)

    async def test_concurrent_close_escalates_once(self):
        self.code = "import time; time.sleep(60)"
        process = await self.process().start()
        child = self.children[0]
        # Exercise the timeout path on Windows too, where SIGTERM cannot be ignored.
        with (
            patch.object(child, "terminate") as terminate,
            patch.object(child, "kill", wraps=child.kill) as kill,
        ):
            await asyncio.gather(process.close(), process.close())
        terminate.assert_called_once()
        kill.assert_called_once()
        self.assertIsNotNone(child.returncode)

    async def test_bundled_executable_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            name = "vfspy-native.exe" if os.name == "nt" else "vfspy-native"
            binary = package / "_bin" / name
            binary.parent.mkdir()
            binary.touch()
            with patch("vfspy.process._executable.__file__", package / "process/_executable.py"):
                process = self.process(executable=None)
            await process.start()
            self.assertEqual(self.calls[0][0][0], str(binary))

    async def test_missing_bundled_binary_is_actionable(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch(
                "vfspy.process._executable.__file__", Path(folder) / "process/_executable.py"
            ):
                with self.assertRaisesRegex(FileNotFoundError, "build_bridge.py"):
                    self.process(executable=None)
            self.assertEqual(self.calls, [])

    async def test_start_can_be_retried_after_spawn_failure(self):
        process = self.process()
        with patch(
            "vfspy.process.manager.asyncio.create_subprocess_exec",
            side_effect=OSError("cannot execute"),
        ):
            with self.assertRaisesRegex(OSError, "cannot execute"):
                await process.start()
        await process.close()
        self.assertEqual(self.children, [])
        self.assertIs(await process.start(), process)

    async def test_close_before_start_allows_start(self):
        process = self.process()
        await process.close()
        await process.close()
        self.assertEqual(self.calls, [])
        self.assertIs(await process.start(), process)

    async def test_concurrent_start_is_rejected(self):
        self.code = "import time; time.sleep(60)"
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        duplicate = asyncio.create_task(process.start())
        self.spawn_release.set()
        await starting
        with self.assertRaises(RuntimeError):
            await duplicate
        self.assertEqual(len(self.calls), 1)


def config(**overrides):
    return BridgeProcessConfig(
        **{
            "mountpoint": "Z:",
            "endpoint": "127.0.0.1:12345",
            "token": "child-secret",
            **overrides,
        }
    )


class BridgeProcessConfigTests(unittest.TestCase):
    def test_defaults_and_path_conversion(self):
        settings = config()
        self.assertEqual(settings.max_message_bytes, 16 << 20)
        self.assertEqual(settings.startup_timeout, 25)
        self.assertEqual(settings.stop_timeout, 5)
        self.assertIsNone(settings.executable)
        self.assertEqual(config(executable="bin/bridge").executable, Path("bin/bridge"))
        self.assertEqual(config(startup_timeout=1, stop_timeout=2).stop_timeout, 2.0)

    def test_model_is_immutable_and_rejects_unknown_fields(self):
        settings = config()
        with self.assertRaises(ValidationError):
            settings.stop_timeout = 1
        with self.assertRaises(ValidationError):
            config(unknown=True)
        self.assertNotIn("child-secret", repr(settings))

    def test_endpoint_port_boundaries(self):
        for port in (1, 9, 10, 9999, 10000, 59999, 60000, 64999, 65000, 65499, 65500, 65535):
            with self.subTest(port=port):
                endpoint = f"127.0.0.1:{port}"
                self.assertEqual(config(endpoint=endpoint).endpoint, endpoint)
        self.assertEqual(config(endpoint="127.0.0.1:00001").endpoint, "127.0.0.1:00001")

    def test_invalid_configuration(self):
        for field, values in (
            (
                "endpoint",
                [
                    "localhost:1234",
                    "127.0.0.1:0",
                    "127.0.0.1:65536",
                    "127.0.0.1:1x",
                    "127.0.0.1:99999",
                    "127.0.0.1:00000",
                    "127.0.0.1:1\n",
                    123,
                ],
            ),
            ("token", ["", "bad\x00token", 123]),
            ("mountpoint", ["", "bad\x00path", 123]),
            ("max_message_bytes", [0, -1, 1 << 31, True, 1.5, "1024"]),
            ("startup_timeout", [0, -1, float("nan"), float("inf"), 1e308, True, "25s"]),
            ("stop_timeout", [0, -1, float("nan"), float("inf"), True]),
        ):
            for value in values:
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(ValidationError) as exc,
                ):
                    config(**{field: value})
                self.assertEqual(exc.exception.errors()[0]["loc"], (field,))
