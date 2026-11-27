"""Regenerate both language bindings with pinned generators."""

import os
import re
import subprocess
import sys

from build_bridge import PACKAGE_ROOT, shared_root


def main():
    root = shared_root()
    proto = root / "proto"
    tool_bin = root / ".tools" / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOBIN": str(tool_bin)}
    for module in (
        "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.6",
        "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1",
    ):
        executable = module.split("/")[-1].split("@")[0] + (".exe" if os.name == "nt" else "")
        if not (tool_bin / executable).exists():
            subprocess.run(["go", "install", module], check=True, env=env)
    python_out = PACKAGE_ROOT / "src/vfspy/_rpc"
    go_out = root / "native/internal/rpc"
    python_out.mkdir(parents=True, exist_ok=True)
    go_out.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    sources = sorted(proto.glob("*.proto"))
    protoc = [sys.executable, "-m", "grpc_tools.protoc", f"-I{proto}"]
    subprocess.run(
        [
            *protoc,
            f"--python_out={python_out}",
            f"--pyi_out={python_out}",
            f"--plugin=protoc-gen-go={tool_bin / ('protoc-gen-go' + suffix)}",
            f"--go_out={go_out}",
            "--go_opt=paths=source_relative",
            *sources,
        ],
        check=True,
    )
    subprocess.run(
        [
            *protoc,
            f"--grpc_python_out={python_out}",
            f"--plugin=protoc-gen-go-grpc={tool_bin / ('protoc-gen-go-grpc' + suffix)}",
            f"--go-grpc_out={go_out}",
            "--go-grpc_opt=paths=source_relative",
            proto / "controller.proto",
        ],
        check=True,
    )

    bindings = [
        *python_out.glob("*_pb2.py"),
        *python_out.glob("*_pb2.pyi"),
        python_out / "controller_pb2_grpc.py",
    ]
    for binding in bindings:
        contents = binding.read_text(encoding="utf-8")
        contents = re.sub(r"^import (\w+_pb2) as ", r"from . import \1 as ", contents, flags=re.M)
        binding.write_text(contents, encoding="utf-8")

    # Remove obsolete service bindings from checkouts generated before the split.
    (python_out / "session_pb2_grpc.py").unlink(missing_ok=True)
    (go_out / "session_grpc.pb.go").unlink(missing_ok=True)
    (python_out / "__init__.py").touch()


if __name__ == "__main__":
    main()
