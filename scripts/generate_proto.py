"""Regenerate both language bindings with pinned generators."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    tool_bin = ROOT / ".tools" / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOBIN": str(tool_bin)}
    for module in (
        "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.6",
        "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1",
    ):
        subprocess.run(["go", "install", module], check=True, env=env)
    python_out = ROOT / "src/vfspy/_rpc"
    go_out = ROOT / "native/internal/rpc"
    python_out.mkdir(parents=True, exist_ok=True)
    go_out.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{ROOT / 'proto'}",
            f"--python_out={python_out}",
            f"--pyi_out={python_out}",
            f"--grpc_python_out={python_out}",
            f"--plugin=protoc-gen-go={tool_bin / ('protoc-gen-go' + suffix)}",
            f"--plugin=protoc-gen-go-grpc={tool_bin / ('protoc-gen-go-grpc' + suffix)}",
            f"--go_out={go_out}",
            "--go_opt=paths=source_relative",
            f"--go-grpc_out={go_out}",
            "--go-grpc_opt=paths=source_relative",
            str(ROOT / "proto/session.proto"),
        ],
        check=True,
    )
    binding = python_out / "session_pb2_grpc.py"
    binding.write_text(
        binding.read_text().replace("import session_pb2 as", "from . import session_pb2 as"),
        encoding="utf-8",
    )
    (python_out / "__init__.py").touch()


if __name__ == "__main__":
    main()
