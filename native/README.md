# Go 原生文件系统进程

Go 程序负责内存中的目录树、文件内容、inode、句柄、容量限制与 WinFsp/FUSE 挂载。系统文件读写直接在 Go 中完成，文件变更通过 gRPC 上报给控制进程。

入口为 [`main.go`](main.go)，Go 模块名为 `vfspy/native`，`go.mod` 要求 Go 1.23.0。控制通信的接口、鉴权、消息和生命周期统一见 [RPC 文档](../docs/rpc.md)。

## 构建

以下 Python 示例均在项目根目录执行。使用当前 Python 解释器调用统一构建脚本：

```python
import subprocess
import sys

subprocess.run([sys.executable, "scripts/build_bridge.py"], check=True)
```

脚本会自动创建 `bin/` 目录，调用 `go build -trimpath`，并在成功后打印产物的绝对路径。Windows 产物为 `bin/vfspy-native.exe`，其他平台为 `bin/vfspy-native`。脚本不接收命令行参数。

构建需要 Python 3.11+ 和 Go 工具链，`go` 命令需在 PATH 中。Windows 下脚本自动设置 `CGO_ENABLED=0`；其他平台使用 Go 默认环境，需要相应的 FUSE 运行库、开发头文件和 C 工具链。Windows 实际挂载需要安装 WinFsp 运行时。

## 启动与命令行参数

启动前需准备好控制服务，接入流程见 [RPC 文档](../docs/rpc.md)。下面的示例假定控制服务已监听 `127.0.0.1:50051`、使用相同 token；Windows 下 `Z:` 空闲，其他平台下 `/mnt/vfspy` 是可用的挂载目录：

```python
import os
import subprocess
from pathlib import Path

root = Path.cwd()
executable = "vfspy-native.exe" if os.name == "nt" else "vfspy-native"
bridge = root / "bin" / executable
env = {**os.environ, "VFSPY_TOKEN": "replace-with-controller-session-token"}

subprocess.run(
    [
        str(bridge),
        "--endpoint", "127.0.0.1:50051",
        "--mountpoint", "Z:" if os.name == "nt" else "/mnt/vfspy",
        "--max-message-bytes", "16777216",
        "--startup-timeout", "25s",
    ],
    env=env,
    check=True,
)
```

`env` 参数仅向子进程传入 token，不修改当前 Python 进程的环境变量。`subprocess.run` 会等待 Go 进程退出；就绪状态和会话完成的判断方式见 [RPC 文档](../docs/rpc.md)。

| 参数 | 默认值 | 输入要求与用途 |
| --- | --- | --- |
| `--endpoint` | 空 | 必填，格式为 `127.0.0.1:端口`；通过 `net.SplitHostPort` 后主机必须恰为 `127.0.0.1`，不能写 `localhost`、IPv6 或远程地址。端口需能连接控制服务。 |
| `--mountpoint` | 空 | 必填，Windows 可传空闲盘符如 `Z:`；其他平台传系统支持的挂载路径。这里只校验非空，实际可用性由挂载层决定。 |
| `--max-message-bytes` | `16777216`（16 MiB） | 整数（字节数），设置客户端单条 gRPC 消息的发送和接收上限，包含 protobuf 编码开销；不是文件容量限制。应为正数并与控制服务配置匹配；当前参数解析后没有独立的正数校验。 |
| `--startup-timeout` | `25s` | 必须大于零，Go duration 格式，例如 `500ms`、`25s`、`1m`；设置初始化超时，具体作用范围见 [RPC 文档](../docs/rpc.md)。 |
| `-h` / `--help` | — | 输出参数帮助后以退出码 0 结束。 |

采用标准 Go `flag` 解析，支持 `-name=value`、`--name=value` 和 `--name value`。没有定义位置参数；首个位置参数会终止 flag 解析，剩余参数没有业务含义。没有透传任意 FUSE 参数的入口。

### 环境变量与标准输入

`VFSPY_TOKEN` 为必填的会话 token，通过子进程环境传入；鉴权与传输规则见 [RPC 文档](../docs/rpc.md)。

程序不读取 stdin，不从命令行接收文件树或 JSON。初始化数据由控制服务提供，格式见 [RPC 文档](../docs/rpc.md)。

## 输出

| 通道 | 内容 |
| --- | --- |
| 控制通信 | 运行状态与文件变更通知；消息定义和交互流程见 [RPC 文档](../docs/rpc.md)。 |
| 挂载路径 | 面向操作系统的普通文件/目录接口；内容只保存在该 Go 进程内存中，没有持久化。 |
| stderr | `slog` 的逐行 JSON 日志，包括 `time`、`level`、`msg`、`pid`；进入 `run()` 后增加 `mountpoint`，错误通常带 `error`。日志级别包含 DEBUG。标准 flag 的帮助/参数错误是普通文本，底层挂载库也可能输出诊断。 |
| stdout | Go 入口未定义业务输出或 JSON 协议。构建脚本的 stdout 会打印产物路径。 |

典型日志消息包括 `bridge starting`、`initial data imported`（`entries`）、`filesystem mounted`、`unmount requested`、`bridge stopped`；失败时输出 `bridge failed` 和错误。连接断开或卸载请求失败可能记录 WARN。

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | `run()` 正常返回，或请求参数帮助。 |
| `1` | `run()` 返回错误，例如配置无效、初始化失败、挂载失败、通知发送失败，或卸载后的通知流未在限定时间内排空。 |
| `2` | 标准 flag 解析错误，例如未知选项或 duration 格式错误。 |

崩溃或强制终止的退出状态由操作系统决定。退出码 0 本身不代表会话全过程成功，判断方式见 [RPC 文档](../docs/rpc.md)。

## 挂载选项

固定挂载选项：`-f`、`volname=vfspy`、`attr_timeout=0`、`entry_timeout=0`、`negative_timeout=0`；Windows 另加 `uid=-1,gid=-1`。大小写行为由初始化配置决定。

## Go 检查

在项目根目录通过 Python 执行 Go 单元测试和静态检查：

```python
import os
import subprocess
from pathlib import Path

root = Path.cwd()
env = os.environ.copy()
if os.name == "nt":
    env["CGO_ENABLED"] = "0"

for command in (["go", "test", "./..."], ["go", "vet", "./..."]):
    subprocess.run(command, cwd=root / "native", env=env, check=True)
```

`internal/memfs` 包含内存文件系统的 Go 单元测试。上述检查不执行真实挂载。
