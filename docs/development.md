# 开发说明

## 构建

构建 Go 需要 Go 工具链；运行挂载还需要 WinFsp/FUSE。

```console
uv run python scripts/build_bridge.py
uv build
```

`uv build` 会在构建 wheel 时自动调用 Go 构建脚本并收录本次编译的二进制；sdist 包含 Go 源码与构建脚本，可从源码重新构建。`uv sync` 的 editable 安装不编译或捆绑 bridge，开发时仍可使用独立脚本生成 `bin/` 下的程序。

发布工作流使用 cibuildwheel 4.3.5 构建 Windows x64 和 Linux x86_64（manylinux_2_28）wheel，再与 sdist 一起发布。Linux 构建容器会安装 Go 1.27.4 和 FUSE 开发头文件，并通过 auditwheel 修复平台标签。本地可运行：

```console
uvx cibuildwheel==4.3.5 --output-dir dist
```

Windows 需先将 Go 加入 PATH；Linux wheel 构建需运行 Docker。cibuildwheel 未配置额外测试命令。

### Wheel 发布

构建发布的 wheel 包内包含 Go bridge，Windows 路径为 `vfspy/_bin/vfspy-native.exe`，Linux 路径为 `vfspy/_bin/vfspy-native`。可通过包资源定位：

```python
import os
from importlib.resources import files

bridge = files("vfspy").joinpath("_bin", "vfspy-native.exe" if os.name == "nt" else "vfspy-native")
```

wheel 使用 `py3-none-<platform>` 标签，支持 Python 3.11+，不绑定特定 CPython ABI。安装 wheel 不需要 Go 工具链，实际挂载仍需 WinFsp/FUSE 运行时。

## 协议生成

生成脚本需要 Go 和开发依赖，使用固定的 `grpcio-tools==1.78.0`、`protoc-gen-go@v1.36.6`、`protoc-gen-go-grpc@v1.5.1`，首次安装 Go 生成器需要下载依赖。现有绑定可直接导入，不必重新生成。

```console
uv run python scripts/generate_proto.py
```
