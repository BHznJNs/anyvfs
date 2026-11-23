import unittest
from pathlib import Path

from pydantic import ValidationError

from ._helpers import config


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
