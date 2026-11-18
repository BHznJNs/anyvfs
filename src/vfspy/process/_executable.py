import os
from pathlib import Path


def bundled_bridge_executable() -> Path:
    """Locate the bridge bundled in the package's _bin directory."""
    name = "vfspy-native.exe" if os.name == "nt" else "vfspy-native"
    executable = Path(__file__).resolve().parent.parent / "_bin" / name
    if not executable.is_file():
        raise FileNotFoundError(
            "bundled native bridge not found; build it with "
            "scripts/build_bridge.py and set BridgeProcessConfig(executable=...)"
        )
    return executable
