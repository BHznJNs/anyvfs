"""Include the shared Go bridge in Python distributions."""

import sysconfig
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Protocol, cast

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class _BuildBridgeModule(Protocol):
    def shared_root(self) -> Path: ...

    def build_bridge(self) -> Path: ...


def _load_build_bridge_module(root: Path) -> _BuildBridgeModule:
    """Load the bridge builder from the project scripts directory."""
    spec = spec_from_file_location("vfspy_build_bridge", root / "scripts/build_bridge.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load bridge builder from {root / 'scripts/build_bridge.py'}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_BuildBridgeModule, module)


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return

        # Isolated build environments do not put scripts/ on the import path.
        package_root = Path(self.root).resolve()
        module = _load_build_bridge_module(package_root)
        if self.target_name == "sdist":
            root = module.shared_root()
            if root != package_root:
                for directory in ("native", "proto", "docs"):
                    build_data["force_include"][str(root / directory)] = directory
            return

        bridge = module.build_bridge()
        build_data["force_include"][str(bridge)] = f"vfspy/_bin/{bridge.name}"
        build_data["pure_python"] = False
        platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        build_data["tag"] = f"py3-none-{platform}"
