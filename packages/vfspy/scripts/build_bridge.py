"""Build the native Go bridge into bin/ for standalone use or wheel packaging."""

import os
import subprocess
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def shared_root() -> Path:
    """Locate the shared sources in either an unpacked sdist or the checkout."""
    if (PACKAGE_ROOT / "native/go.mod").is_file():
        return PACKAGE_ROOT
    return PACKAGE_ROOT.parents[1]


def build_bridge() -> Path:
    """Compile the bridge and return the absolute path to its executable."""
    root = shared_root()
    suffix = ".exe" if os.name == "nt" else ""
    output = root / "bin" / ("anyvfs-native" + suffix)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if os.name == "nt":
        env["CGO_ENABLED"] = "0"
    subprocess.run(
        ["go", "build", "-mod=readonly", "-trimpath", "-o", str(output), "."],
        cwd=root / "native",
        env=env,
        check=True,
    )
    output.chmod(output.stat().st_mode | 0o111)
    return output


if __name__ == "__main__":
    print(build_bridge())
