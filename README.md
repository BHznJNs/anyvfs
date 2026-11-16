# vfspy

vfspy 是基于 Go 内存文件系统与 gRPC 控制协议的项目。Python 包名为 `vfspy`，Go 模块名为 `vfspy/native`。

Go 负责文件系统状态与 WinFsp/FUSE 挂载；控制进程通过 gRPC 提供初始化数据、接收变更事件并管理会话。Python 包提供 `vfspy._rpc` 生成绑定，控制服务由应用实现。

## 目录与文档

| 路径 | 内容 |
| --- | --- |
| [`native/README.md`](native/README.md) | Go 构建、命令行输入、环境变量、输出及生命周期 |
| [`docs/rpc.md`](docs/rpc.md) | 全部 gRPC 接口、消息字段、方向、顺序和故障语义 |
| `proto/session.proto` | 共享协议源文件，命名空间为 `vfspy` |
| `src/vfspy/_rpc/` | Python protobuf/gRPC 生成绑定 |
| `native/internal/rpc/` | Go protobuf/gRPC 生成绑定 |
| `native/internal/memfs/` | Go 内存文件系统与单元测试 |
| `scripts/` | 独立构建 Go 程序及重新生成绑定的工具 |

## Python 绑定

```console
uv sync --locked
```

```python
from vfspy._rpc import session_pb2 as pb
from vfspy._rpc import session_pb2_grpc as rpc
```

`rpc.ControllerServicer` 是生成的服务基类，方法默认返回 `UNIMPLEMENTED`。需要自行实现控制服务、鉴权、初始数据和会话处理，再启动 Go 进程；当前没有可直接挂载的 Python 高层 API。Python wheel 仅包含 Python 包，Go 源码和程序需从项目单独构建。

## 构建与协议生成

```console
uv run python scripts/build_bridge.py
uv run python scripts/generate_proto.py
uv build
```

构建 Go 需要 Go 工具链；运行挂载还需要 WinFsp/FUSE。生成脚本需要 Go 和开发依赖，使用固定的 `grpcio-tools==1.78.0`、`protoc-gen-go@v1.36.6`、`protoc-gen-go-grpc@v1.5.1`，首次安装 Go 生成器需要下载依赖。现有绑定可直接导入，不必重新生成。
