"""Build the native Go bridge into bin/ for standalone use or wheel packaging."""

import os
import subprocess
from pathlib import Path


def build_bridge() -> Path:
    """Compile the bridge and return the absolute path to its executable."""
    root = Path(__file__).resolve().parents[1]
    suffix = ".exe" if os.name == "nt" else ""
    output = root / "bin" / ("vfspy-native" + suffix)
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
