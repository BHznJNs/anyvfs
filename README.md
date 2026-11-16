# vfspy

vfspy 是基于 Go 内存文件系统与 gRPC 控制协议的项目。Python 包名为 `vfspy`，Go 模块名为 `vfspy/native`。

Go 负责文件系统状态与 WinFsp/FUSE 挂载；控制进程通过 gRPC 提供初始化数据、接收变更事件并管理会话。Python 包提供 `vfspy._rpc` 生成绑定，控制服务由应用实现。

## Python 绑定

```console
uv sync --locked
```

```python
from vfspy._rpc import session_pb2 as pb
from vfspy._rpc import session_pb2_grpc as rpc
```

`rpc.ControllerServicer` 是生成的服务基类，方法默认返回 `UNIMPLEMENTED`。需要自行实现控制服务、鉴权、初始数据和会话处理，再启动 Go 进程；当前没有可直接挂载的 Python 高层 API。发行 wheel 内包含 Go bridge，Windows 路径为 `vfspy/_bin/vfspy-native.exe`，Linux 路径为 `vfspy/_bin/vfspy-native`。可通过包资源定位：

```python
import os
from importlib.resources import files

bridge = files("vfspy").joinpath("_bin", "vfspy-native.exe" if os.name == "nt" else "vfspy-native")
```

wheel 使用 `py3-none-<platform>` 标签，支持 Python 3.11+，不绑定特定 CPython ABI。安装 wheel 不需要 Go 工具链，实际挂载仍需 WinFsp/FUSE 运行时。

## 构建与协议生成

```console
uv run python scripts/build_bridge.py
uv run python scripts/generate_proto.py
uv build
```

`uv build` 会在构建 wheel 时自动调用 Go 构建脚本并收录本次编译的二进制；sdist 包含 Go 源码与构建脚本，可从源码重新构建。`uv sync` 的 editable 安装不编译或捆绑 bridge，开发时仍可使用独立脚本生成 `bin/` 下的程序。

发布工作流使用 cibuildwheel 4.3.5 构建 Windows x64 和 Linux x86_64（manylinux_2_28）wheel，再与 sdist 一起发布。Linux 构建容器会安装 Go 1.27.4 和 FUSE 开发头文件，并通过 auditwheel 修复平台标签。本地可运行：

```console
uvx cibuildwheel==4.3.5 --output-dir dist
```

Windows 需先将 Go 加入 PATH；Linux wheel 构建需运行 Docker。cibuildwheel 未配置额外测试命令。

构建 Go 需要 Go 工具链；运行挂载还需要 WinFsp/FUSE。生成脚本需要 Go 和开发依赖，使用固定的 `grpcio-tools==1.78.0`、`protoc-gen-go@v1.36.6`、`protoc-gen-go-grpc@v1.5.1`，首次安装 Go 生成器需要下载依赖。现有绑定可直接导入，不必重新生成。
