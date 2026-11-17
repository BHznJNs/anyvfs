"""Expose bundled hooks through PyInstaller's package entry point."""

from pathlib import Path


def get_hook_dirs() -> list[str]:
    return [str(Path(__file__).resolve().parent)]
