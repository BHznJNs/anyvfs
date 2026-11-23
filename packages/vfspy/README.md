# vfspy

`vfspy` 是 AnyVFS 的 Python 客户端库，支持 Python 3.11 及以上版本。它管理 Go 原生桥接进程，通过 gRPC 提供挂载配置并接收文件系统变更通知。

```console
pip install vfspy
```

发布的 wheel 自带原生桥接程序。实际挂载需要 Windows 上的 WinFsp 或 Linux 上的 FUSE。

## 基础示例

`examples/basic.py` 在临时目录中挂载内存文件系统，读取并改写预置的 `hello.txt`，打印文件变更通知后卸载。安装 wheel 后，在 `packages/vfspy/` 目录中运行以下命令。

```console
python examples/basic.py
```

从仓库源码运行时，先生成 RPC 绑定并构建 Go 桥接程序，再传入桥接程序的路径。以下命令使用 Windows 文件名，Linux 上省略 `.exe`。

```console
uv run python scripts/generate_proto.py
uv run python scripts/build_bridge.py
uv run python examples/basic.py --executable ../../bin/anyvfs-native.exe
```
