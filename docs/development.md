# 开发说明

Python 客户端的源码、测试和打包配置位于 `packages/vfspy/`。仓库根目录的 `native/` 和 `proto/` 提供共享的 Go 桥接程序和 RPC 协议。除 cibuildwheel 命令外，以下命令均在 `packages/vfspy/` 下运行。

## 协议生成

生成脚本需要 Go 和开发依赖，使用固定的 `grpcio-tools==1.78.0`、`protoc-gen-go@v1.36.6`、`protoc-gen-go-grpc@v1.5.1`，首次安装 Go 生成器需要下载依赖。Python 和 Go 的 RPC 生成文件不纳入 Git。首次检出或修改 `proto/` 下的协议文件后，需在导入 Python 绑定、运行 Go 测试或构建前执行生成脚本。

```console
uv run python scripts/generate_proto.py
```

wheel 包含 Python 绑定，sdist 包含 Python 和 Go 绑定，因此安装发布包无需重新生成 RPC 代码；从 sdist 构建 wheel 仍需 Go 工具链和对应平台的 FUSE 开发依赖。

## Python 测试与检查

```console
uv sync --locked --all-groups
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync ruff check src scripts tests
uv run --no-sync ruff format --check src scripts tests
```

首次检出需先生成 RPC 绑定。测试命令自动发现并运行 `tests/` 下的全部 Python 测试，覆盖 RPC 协议交互、Pydantic 配置校验与不可变性，以及进程启动、退出、取消与终止回收。RPC 测试用 Python 客户端模拟 Go 通知流，进程测试用 Python 子进程模拟 bridge。从外部进程操作真实挂载点到 Python 收到通知的整条链路需要用 WinFsp/FUSE 单独验证。

## 构建

构建 Go 需要 Go 工具链；运行挂载还需要 WinFsp/FUSE。

```console
uv sync --locked --all-groups
uv run --no-sync python scripts/generate_proto.py
uv run python scripts/build_bridge.py
uv build
```

`uv build` 会在构建 wheel 时自动调用 Go 构建脚本并收录本次编译的二进制；sdist 包含 Go 源码与构建脚本，可从源码重新构建。`uv sync` 的 editable 安装不编译或捆绑 bridge，开发时仍可使用独立脚本生成 `bin/` 下的程序。

独立桥接程序写入仓库根目录的 `bin/`，Python 分发包写入 `packages/vfspy/dist/`。

发布工作流使用 cibuildwheel 4.3.5 构建 Windows x64 和 Linux x86_64（manylinux_2_28）wheel，再与 sdist 一起发布。Linux 构建容器会安装 Go 1.27.4 和 FUSE 开发头文件，并通过 auditwheel 修复平台标签。本地可在仓库根目录运行：

```console
uvx cibuildwheel==4.3.5 packages/vfspy --output-dir packages/vfspy/dist
```

运行 cibuildwheel 前需先生成 RPC 绑定。Windows 需先将 Go 加入 PATH；Linux wheel 构建需运行 Docker。cibuildwheel 未配置额外测试命令。

### Wheel 发布

构建发布的 wheel 包内包含 Go bridge，Windows 路径为 `vfspy/_bin/anyvfs-native.exe`，Linux 路径为 `vfspy/_bin/anyvfs-native`。可通过包资源定位：

```python
import os
from importlib.resources import files

bridge = files("vfspy").joinpath("_bin", "anyvfs-native.exe" if os.name == "nt" else "anyvfs-native")
```

wheel 使用 `py3-none-<platform>` 标签，支持 Python 3.11+，不绑定特定 CPython ABI。安装 wheel 不需要 Go 工具链，实际挂载仍需 WinFsp/FUSE 运行时。

### PyInstaller 打包

vfspy 通过 `pyinstaller40` entry point 注册随包发布的 hook。下游项目在打包环境中安装 vfspy wheel 后，只要代码导入 `vfspy` 或其子模块，PyInstaller 就会自动将 native bridge 作为二进制收录到 `vfspy/_bin/`，无需额外指定 `--additional-hooks-dir` 或 `--add-binary`：

```console
python -m pip install /path/to/vfspy-0.1.0-py3-none-win_amd64.whl pyinstaller
python -m PyInstaller --onefile app.py
```

`--onedir` 同样适用，`BridgeProcessConfig.executable` 保持默认值即可通过包资源找到 bridge。打包时需安装与目标平台匹配的 wheel；目标机器实际挂载仍需 WinFsp/FUSE 运行时。

editable 安装不包含 `vfspy/_bin/` 中的 bridge，因此 hook 不会收录开发目录 `bin/` 下的独立构建产物。验证下游打包时应先构建并安装 wheel。
