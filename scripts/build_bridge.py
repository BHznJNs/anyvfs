"""Build the native Go bridge into bin/ independently of the Python package."""

import os
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    suffix = ".exe" if os.name == "nt" else ""
    output = root / "bin" / ("vfspy-native" + suffix)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if os.name == "nt":
        env["CGO_ENABLED"] = "0"
    subprocess.run(
        ["go", "build", "-trimpath", "-o", str(output), "."],
        cwd=root / "native",
        env=env,
        check=True,
    )
    print(output)


if __name__ == "__main__":
    main()
