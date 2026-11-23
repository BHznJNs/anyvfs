import unittest

from pydantic import ValidationError

from vfspy.server import InitialDirectory, InitialFile, RpcServerConfig


def settings(**overrides):
    return RpcServerConfig(
        **{
            "max_file_bytes": 1024,
            "max_total_bytes": 4096,
            **overrides,
        }
    )


class ConfigurationTests(unittest.TestCase):
    def test_flat_settings_and_entry_defaults(self):
        value = settings(
            entries=[
                {"path": "/hello", "data": b"hello"},
                {"path": "/empty-file"},
                InitialDirectory(path="/empty-directory"),
            ]
        )
        self.assertEqual(value.max_file_bytes, 1024)
        self.assertEqual(value.max_total_bytes, 4096)
        self.assertIsInstance(value.entries, tuple)
        self.assertEqual(
            value.entries,
            (
                InitialFile(path="/hello", data=b"hello"),
                InitialFile(path="/empty-file", data=b""),
                InitialDirectory(path="/empty-directory"),
            ),
        )
        self.assertEqual(value.max_message_bytes, 16 << 20)
        self.assertTrue(value.case_sensitive)
        self.assertEqual(RpcServerConfig.model_validate(value.model_dump()), value)

    def test_invalid_field_types_and_bounds(self):
        for field, invalid in (
            ("max_file_bytes", [0, -1, (1 << 30) + 1, True, 1.5, "1024"]),
            ("max_total_bytes", [0, -1, (1 << 40) + 1, False, 1.5, "4096"]),
            ("case_sensitive", [0, 1, "true"]),
        ):
            for item in invalid:
                with self.subTest(field=field, value=item), self.assertRaises(ValidationError):
                    settings(**{field: item})
        for item in (0, -1, 1 << 31, True, 1.5, "1024"):
            with self.subTest(max_message_bytes=item), self.assertRaises(ValidationError):
                settings(max_message_bytes=item)

    def test_nested_validation_error_locations(self):
        with self.assertRaises(ValidationError) as raised:
            settings(entries=[{"path": "/hello", "data": "text"}])
        self.assertIn(
            ("entries", 0, "InitialFile", "data"),
            [error["loc"] for error in raised.exception.errors()],
        )

    def test_path_shape_and_entry_types(self):
        for entry in (
            {"path": ""},
            {"path": "/"},
            {"path": "relative"},
            {"path": 123},
            {"path": "/x", "directory": "yes"},
            {"path": "/x", "directory": True},
            {"path": "/x", "unknown": True},
        ):
            with self.subTest(entry=entry), self.assertRaises(ValidationError):
                settings(entries=[entry])
        with self.assertRaises(ValidationError):
            InitialDirectory.model_validate({"path": "/x", "data": b""})

    def test_unknown_fields_and_connection_overrides_rejected(self):
        for extra in (
            {"token": "fixed"},
            {"port": 1234},
            {"unknown": 1},
            {"config": {"max_file_bytes": 1024, "max_total_bytes": 4096}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                settings(**extra)

    def test_config_and_entries_are_immutable_snapshots(self):
        data = {"max_file_bytes": 1024, "max_total_bytes": 4096}
        entries = [{"path": "/hello", "data": b"hello"}]
        value = settings(**data, entries=entries)
        data["max_file_bytes"] = 1
        entries[0]["data"] = b"changed"
        entries.clear()
        self.assertEqual(value.max_file_bytes, 1024)
        self.assertEqual(value.entries[0].data, b"hello")
        for model, field, replacement in (
            (value, "max_message_bytes", 32),
            (value, "max_file_bytes", 1),
            (value.entries[0], "data", b"changed"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                setattr(model, field, replacement)
