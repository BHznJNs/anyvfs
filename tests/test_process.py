import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from vfspy.process import BridgeProcess, BridgeProcessConfig, BridgeProcessError


class BridgeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.code = "pass"
        self.calls = []
        self.processes = []
        self.spawn_entered = asyncio.Event()
        self.spawn_release = asyncio.Event()
        self.spawn_release.set()
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            process = await real_spawn(sys.executable, "-c", self.code, *args[1:], **kwargs)
            self.spawn_entered.set()
            await self.spawn_release.wait()
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

    async def wait_stderr(self, process, marker):
        async with asyncio.timeout(5):
            while marker not in process.stderr:
                await asyncio.sleep(0.01)

    async def test_arguments_environment_and_normal_exit(self):
        self.code = (
            "import json,os,sys; "
            "sys.stderr.write(json.dumps([sys.argv[1:], os.environ['VFSPY_TOKEN']]))"
        )
        mountpoint = "mount dir & $(literal)"
        process = self.process(mountpoint=mountpoint, max_message_bytes=2048, startup_timeout=0.125)
        self.assertIsNone(process.pid)
        self.assertIsNone(process.returncode)
        self.assertFalse(process.running)
        with patch.dict(os.environ, {"VFSPY_TOKEN": "parent-secret"}):
            self.assertIs(await process.start(), process)
            self.assertEqual(os.environ["VFSPY_TOKEN"], "parent-secret")
            self.assertEqual(await process.wait(timeout=5), 0)
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
        arguments, token = json.loads(process.stderr)
        self.assertEqual(arguments, expected)
        self.assertEqual(token, "child-secret")
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, (str(Path(sys.executable).resolve()), *expected))
        self.assertNotIn("child-secret", argv)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(
            kwargs["creationflags"], subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        self.assertIsNotNone(process.pid)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(process.running)
        self.assertEqual(await process.wait(), 0)
        with self.assertRaises(RuntimeError):
            await process.start()

    async def test_failure_includes_diagnostics_and_can_be_unchecked(self):
        self.code = "import sys; sys.stderr.buffer.write(b'mount failed\\n'); sys.exit(7)"
        process = await self.process().start()
        with self.assertRaises(BridgeProcessError) as raised:
            await process.wait(timeout=5)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stderr, "mount failed\n")
        self.assertIn("mount failed", str(raised.exception))
        self.assertEqual(await process.wait(check=False), 7)
        await process.close()

    async def test_output_is_drained_and_diagnostic_memory_is_bounded(self):
        self.code = (
            "import sys; sys.stdout.buffer.write(b'x' * (2 << 20)); "
            "sys.stderr.buffer.write(b'x' * (2 << 20) + b'\\xffend')"
        )
        process = await self.process().start()
        self.assertEqual(await process.wait(timeout=5), 0)
        self.assertEqual(len(process._stderr), 64 << 10)
        self.assertTrue(process.stderr.endswith("\ufffdend"))

    async def test_timeout_and_cancelled_wait_do_not_cancel_shared_exit_monitor(self):
        self.code = "import time; time.sleep(60)"
        process = await self.process().start()
        with self.assertRaises(TimeoutError):
            await process.wait(timeout=0.01)
        waiter = asyncio.create_task(process.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(process.running)
        first = asyncio.create_task(process.wait(check=False))
        second = asyncio.create_task(process.wait(check=False))
        code = await process.kill()
        self.assertEqual(await first, code)
        self.assertEqual(await second, code)
        self.assertIsNotNone(code)
        self.assertFalse(process.running)

    async def test_context_exit_reaps_process_and_preserves_body_error(self):
        self.code = "import time; time.sleep(60)"
        process = self.process()
        with self.assertRaisesRegex(ValueError, "body failure"):
            async with process as value:
                self.assertIs(value, process)
                self.assertTrue(value.running)
                raise ValueError("body failure")
        self.assertFalse(process.running)
        self.assertIsNotNone(process.returncode)
        await asyncio.gather(process.close(), process.close())

    async def test_close_and_start_race_reaps_process(self):
        self.code = "import time; time.sleep(60)"
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        closing = asyncio.create_task(process.close())
        await asyncio.sleep(0)
        self.spawn_release.set()
        with self.assertRaisesRegex(RuntimeError, "closed while starting"):
            await starting
        await closing
        self.assertFalse(process.running)
        self.assertIsNotNone(process.returncode)

    async def test_cancellation_during_creation_reaps_child(self):
        self.code = "import time; time.sleep(60)"
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        starting.cancel()
        await asyncio.sleep(0)
        self.assertFalse(starting.done())
        self.spawn_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertFalse(process.running)
        self.assertIsNotNone(process.returncode)

    async def test_cancellation_during_close_still_reaps_child(self):
        self.code = "import time; time.sleep(60)"
        process = await self.process().start()
        entered, release = asyncio.Event(), asyncio.Event()
        real_terminate = process.terminate

        async def delayed_terminate():
            entered.set()
            await release.wait()
            return await real_terminate()

        with patch.object(process, "terminate", side_effect=delayed_terminate):
            closing = asyncio.create_task(process.close())
            await entered.wait()
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
        self.assertFalse(process.running)
        self.assertIsNotNone(process.returncode)

    @unittest.skipIf(os.name == "nt", "Windows terminate is already forceful")
    async def test_terminate_escalates_to_kill_when_sigterm_is_ignored(self):
        self.code = (
            "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "sys.stderr.write('ready\\n'); sys.stderr.flush(); time.sleep(60)"
        )
        process = await self.process().start()
        await self.wait_stderr(process, "ready")
        self.assertEqual(await process.terminate(timeout=0.05), -signal.SIGKILL)

    async def test_terminate_escalation_and_concurrent_close(self):
        self.code = "import time; time.sleep(60)"
        process = await self.process().start()
        # Exercise the timeout path on Windows too, where SIGTERM cannot be ignored.
        with patch.object(process._process, "terminate") as terminate:
            await asyncio.gather(process.close(), process.close())
        terminate.assert_called_once()
        self.assertFalse(process.running)
        self.assertIsNotNone(process.returncode)

    async def test_bundled_executable_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            name = "vfspy-native.exe" if os.name == "nt" else "vfspy-native"
            binary = package / "_bin" / name
            binary.parent.mkdir()
            binary.touch()
            process = self.process(executable=None)
            with patch("vfspy.process.manager.files", return_value=package):
                await process.start()
                self.assertEqual(await process.wait(timeout=5), 0)
            self.assertEqual(self.calls[0][0][0], str(binary))

    async def test_missing_bundled_binary_is_actionable_and_releases_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            process = self.process(executable=None)
            with patch("vfspy.process.manager.files", return_value=Path(folder)):
                with self.assertRaisesRegex(FileNotFoundError, "build_bridge.py"):
                    await process.start()
            self.assertIsNone(process.pid)
            self.assertEqual(self.calls, [])
            await process.close()
            with self.assertRaises(RuntimeError):
                await process.start()

    async def test_spawn_failure_is_propagated_and_close_remains_safe(self):
        process = self.process()
        with patch(
            "vfspy.process.manager.asyncio.create_subprocess_exec",
            side_effect=OSError("cannot execute"),
        ):
            with self.assertRaisesRegex(OSError, "cannot execute"):
                await process.start()
        await process.close()
        self.assertIsNone(process.pid)

    async def test_operations_before_start_and_close_before_start(self):
        process = self.process()
        for operation in (process.wait, process.terminate, process.kill):
            with self.assertRaisesRegex(RuntimeError, "has not started"):
                await operation()
        await process.close()
        await process.close()
        with self.assertRaises(RuntimeError):
            await process.start()
        self.assertEqual(self.calls, [])

    async def test_concurrent_start_is_rejected(self):
        self.code = "import time; time.sleep(60)"
        self.spawn_release.clear()
        process = self.process()
        starting = asyncio.create_task(process.start())
        await self.spawn_entered.wait()
        with self.assertRaises(RuntimeError):
            await process.start()
        self.spawn_release.set()
        await starting
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
