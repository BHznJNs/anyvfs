# gRPC 进程间通信接口

协议源：[`../proto/session.proto`](../proto/session.proto)。本文统一说明 Go 挂载进程与控制服务之间的接口、鉴权、消息、生命周期和故障语义。构建、启动示例、命令行参数默认值与进程输出见 [Go 进程说明](../native/README.md)。

## 角色与服务

控制进程（可用 Python 或其他语言）是 **gRPC 服务端**；AnyVFS Go 挂载进程是 **gRPC 客户端**，主动连接控制服务，不监听 RPC 端口。proto 使用 `proto3`，package 为 `anyvfs`，service 为 `Controller`。

| 完整 RPC 路径 | 请求 | 响应 | 流类型 | 用途 |
| --- | --- | --- | --- | --- |
| `/anyvfs.Controller/Bootstrap` | `Empty`，Go → 控制进程 | `BootstrapItem`，控制进程 → Go | unary → stream | 一次性下发配置和初始文件树 |
| `/anyvfs.Controller/Session` | `Notice` 流，Go → 控制进程 | `Command` 流，控制进程 → Go | stream ↔ stream | 挂载状态、变更通知及停止命令 |

外部应用通过操作系统的普通文件接口访问挂载路径。Go 挂载进程处理这些请求，把已提交的变更通过 Session 上报给控制进程。访问挂载路径遵循操作系统的访问规则，RPC token 用于 Go 与控制服务之间的鉴权。

连接限定 `127.0.0.1:端口`，无 TLS，Go 禁用代理和自动 RPC 重试。Go 从子进程环境读取非空的 `ANYVFS_TOKEN` 后，立即清除自身环境中的该变量，并在每个 RPC 的 metadata 中附带 `authorization: Bearer <token>`。清除操作不影响父进程环境；Python 启动示例通过 `subprocess.run(..., env=...)` 仅向子进程传入 token。服务端必须校验它；鉴权并未由 proto 或生成的服务基类实现。建议一个 token 对应一次挂载会话，使用不可预测的随机值。

## 1. Bootstrap

Go 发送 `Empty {}`，使用 `--startup-timeout` 指定 deadline，覆盖 Bootstrap 调用及完整响应流接收，不覆盖后续挂载或长期 Session。该选项不能作为整个进程的退出超时。控制服务按顺序返回：

```text
BootstrapItem(config=Config(...))
BootstrapItem(entry=InitialEntry(path="/docs", directory=true))
BootstrapItem(entry=InitialEntry(path="/docs/guide.txt", data=<bytes>))
... EOF（正常结束 Bootstrap 响应流）
```

第一条必须选择 `config`，后续每条必须选择 `entry`；允许只有配置、没有初始文件。根目录由 Go 创建。父目录必须已存在；同一路径不能重复，大小写不敏感模式也不能有折叠后重名。导入不会产生 Event。流错误或无效配置/条目会导致 Go 启动失败。

### 初始化消息（字段名 / protobuf 类型 / 字段号）

| 消息 | 字段 | 语义与约束 |
| --- | --- | --- |
| `Empty` | 无 | Bootstrap 请求。 |
| `Config` | `case_sensitive: bool = 1` | true 按原名比较，false 使用 Go `strings.ToLower` 作为查找键，同时配置挂载宿主的大小写能力。 |
| `Config` | `max_file_bytes: int64 = 2` | 单个文件内容字节上限，必须大于 0、至多 1 GiB（`2^30`）。 |
| `Config` | `max_total_bytes: int64 = 3` | 全部文件内容的字节预算，必须不小于单文件上限、至多 1 TiB（`2^40`）。不是整个进程内存的硬上限。 |
| `InitialEntry` | `path: string = 1` | 挂载根内的绝对路径，例如 `/hello.txt`；使用 `/`，不是宿主机路径或盘符。 |
| `InitialEntry` | `directory: bool = 2` | true 为目录，false 为普通文件。 |
| `InitialEntry` | `data: bytes = 3` | 普通文件的初始完整二进制内容；目录必须为空。不做文本编码转换。 |
| `BootstrapItem` | `config: Config = 1` | `oneof value` 的配置分支，只出现在第一条。 |
| `BootstrapItem` | `entry: InitialEntry = 2` | `oneof value` 的条目分支，出现在配置之后。 |

路径组件不能为空、`.` 或 `..`，组件长度至多 255 字节，禁止反斜杠、冒号、NUL、`< > " | ? *`，禁止以点或空格结尾。使用规范路径，禁止重复 `/`、尾随 `/`；`/` 是已存在的根目录，不能作为新条目。初始文件 mode 为 `S_IFREG | 0644`，目录为 `S_IFDIR | 0755`，没有初始 mode/uid/gid/时间字段。

每个文件内容必须放在一条 `InitialEntry` 中，协议没有初始数据分块接口。客户端和服务端的单消息大小限制必须容纳整个条目及编码开销；`max_file_bytes` 不会自动调整 `--max-message-bytes`。运行中删除但仍被打开的文件，其内容直到最后一个句柄释放才从容量预算中扣除；事件队列等额外开销不在这个预算内。

## 2. Session

Bootstrap 正常结束后，Go 建立一条长期双向流，再执行挂载。控制服务必须并行消费 Notice 请求流和生产 Command 响应流，避免互相等待。

### 通知封装：Go → 控制进程

`Notice` 的下列三个字段属于 `oneof value`，每条只设置一个：

| 字段 | 类型 / 字段号 | 含义 |
| --- | --- | --- |
| `ready` | `Ready = 1` | FUSE `Init` 时排入队列，表示挂载初始化到达就绪点。 |
| `event` | `Event = 2` | 一次已经提交的文件系统变更。 |
| `stopped` | `Stopped = 4` | 挂载调用返回且回调静止后排入队尾，之后 Go 关闭请求流发送方向。挂载失败时也可能发送它，不能单凭它推断曾经 Ready。 |

`Ready {}`、`Stopped {}` 都是无字段消息。普通成功挂载中 Ready 先于运行时事件，Stopped 后于全部已提交事件。服务端应追踪 Ready、revision 和 Stopped，并检查非法或空的 oneof；生成绑定本身不执行状态校验。

### 命令封装：控制进程 → Go

`Command` 的 `oneof value` 只有 `stop` 字段。

| 字段 | 类型 / 字段号 | 行为 |
| --- | --- | --- |
| `stop` | `Stop = 1` | `Stop {}` 无字段。Go 收到后请求卸载，随后尽力排空通知并发送 Stopped。 |

Go 收到空命令或未知命令时会记录告警并请求卸载。

## 3. Event 与全部变更负载

`Event` 的公共字段：

| 字段 | 类型 / 字段号 | 含义 |
| --- | --- | --- |
| `revision` | `uint64 = 1` | 每个新文件系统从 1 开始，提交一个 Event 增加 1；初始化消息和控制消息不占 revision。 |
| `time_ns` | `int64 = 2` | Go 提交事件时的 Unix epoch 纳秒时间；系统时钟可能调整，排序应使用 revision。 |
| `inode` | `uint64 = 3` | 变更目标的 inode，本次会话内分配；重命名后不变，进程重启后不保证一致。 |

以下十个字段属于 `oneof kind`。负载字段均列明 protobuf 类型和字段号；路径是挂载根内的 `/` 路径。mode 包含文件类型位与权限位，不只是权限数字。

| Event 分支 / 字段号 | 消息类型 | 负载全部字段 | 触发与语义 |
| --- | --- | --- | --- |
| `file_created = 10` | `FileCreated` | `path: string = 1`；`mode: uint32 = 2` | 新建普通文件。 |
| `directory_created = 11` | `DirectoryCreated` | `path: string = 1`；`mode: uint32 = 2` | 新建目录。 |
| `file_written = 12` | `FileWritten` | `path: string = 1`；`offset: int64 = 2`；`data: bytes = 3`；`size: int64 = 4`；`linked: bool = 5` | 成功的非空写入；offset 是实际字节偏移（追加写已换算），data 为本次写入的原始字节，size 为写后总长度。稀疏区域在 Go 中补零。 |
| `file_truncated = 13` | `FileTruncated` | `path: string = 1`；`previous_size: int64 = 2`；`size: int64 = 3`；`linked: bool = 4` | 文件长度改变，包括打开时的截断；size 可比旧值大或小。长度不变不发事件。 |
| `entry_renamed = 14` | `EntryRenamed` | `path: string = 1`；`new_path: string = 2`；`directory: bool = 3`；`replaced_inode: uint64 = 4` | path 为旧路径，new_path 为新路径；0 表示未替换其他 inode，非 0 表示被原子替换的目标 inode。子树重命名只发一条，替换不会另发 Removed。公共 inode 为被移动的源对象。 |
| `file_removed = 15` | `FileRemoved` | `path: string = 1` | 解除普通文件的目录链接，path 为删除前路径；打开的 inode 可继续存活。 |
| `directory_removed = 16` | `DirectoryRemoved` | `path: string = 1` | 删除空目录。 |
| `mode_changed = 17` | `ModeChanged` | `path: string = 1`；`mode: uint32 = 2` | mode 实际改变后的完整模式。 |
| `owner_changed = 18` | `OwnerChanged` | `path: string = 1`；`uid: uint32 = 2`；`gid: uint32 = 3` | 属主/组实际改变后的完整值。 |
| `times_changed = 19` | `TimesChanged` | `path: string = 1`；`atime_ns: int64 = 2`；`mtime_ns: int64 = 3` | 显式修改访问/修改时间后，两者的 Unix epoch 纳秒值。写入本身只发 Written，不额外发 TimesChanged；读取不更新 atime。 |

`linked=false` 表示事件对应的打开 inode 已脱离目录树，`path` 是脱离时保存的旧路径；该路径此时可能已被另一个文件复用，应结合公共 inode 区分。重命名后仍有链接的对象使用其当前路径。

文件操作失败不产生相应成功事件。单次系统调用可能导致多种底层操作，不能假设一次应用层保存只对应一个 Event。事件在同一互斥锁内分配 revision 并排队；通知发送由单独协程顺序消费，文件系统操作不等待控制进程回调。控制服务消费缓慢时队列可能增长。

## 4. 生命周期、关闭与错误处理

成功挂载的完整时序：

```text
Bootstrap 完成 → 建立 Session → 挂载 → Ready → 变更事件
→ 卸载 → 剩余已提交事件 → Stopped → 关闭 Go 请求流发送方向 → 退出
```

挂载就绪以 `Notice.ready` 为准。控制服务收到 `Notice.stopped` 时，前面的事件回调均已完成，会话可以结束。挂载失败时也可能出现 Stopped 而没有 Ready，调用方还需单独检查 Go 进程退出状态。

控制服务下发 Stop 后继续读取 Event，收到 Stopped 就结束服务端响应流。服务端以收到 Stopped 为完成条件。收到之前断流会报会话错误，收到之后的断流不影响结果。

收到 `Command.stop`、Session 接收流 EOF/错误、未知命令、发送失败导致上下文取消，或 `os.Interrupt` / `SIGTERM` 时，Go 请求卸载。若此时挂载尚未初始化，卸载协程会等待初始化或挂载调用结束。`host.Mount` 返回且文件系统回调静止后，Go 将 Stopped 放到已提交事件之后，再等待通知排空。连接断开时无法保证 Stopped 或最后几个事件交付。协议没有重连、重放、持久化、恢复点或跨崩溃恰好一次交付机制。

proto 没有自定义错误消息。服务端可通过标准 gRPC status 拒绝请求，例如鉴权失败用 `UNAUTHENTICATED`、重复 Bootstrap 或 Session 顺序错误用 `FAILED_PRECONDITION`；Go 自身的配置/条目/挂载错误写 stderr 并返回进程退出码 1，不会凭空变成服务端 gRPC 错误响应。

服务端应限制单会话的 Bootstrap/Session 消费次数，检查初始化顺序、token 和消息大小，并拒绝无效 oneof/未知事件。Go 当前只调用一次 Bootstrap 和一次 Session，没有自动重试。proto3 标量未赋值会使用默认值（false/0/空串/空 bytes），业务约束仍需显式检查；oneof 分支可通过 `WhichOneof`（Python）或生成的包装类型（Go）识别。

## 5. 生成绑定与最小集成流程

| 语言 | 路径 | 生成入口 |
| --- | --- | --- |
| Python | `packages/vfspy/src/vfspy/_rpc/` 下的 `session_pb2.py`、`session_pb2.pyi`、`session_pb2_grpc.py` | `pb.Config` 等消息，`rpc.ControllerServicer`、`rpc.add_ControllerServicer_to_server`、`rpc.ControllerStub` |
| Go | `native/internal/rpc/session.pb.go`、`session_grpc.pb.go` | `rpc.NewControllerClient` 及消息类型；Go 生成包声明为 `rpc`，import 路径是 `anyvfs/native/internal/rpc` |

Python 导入：

```python
from vfspy._rpc import session_pb2 as pb
from vfspy._rpc import session_pb2_grpc as rpc
```

1. 实现 `ControllerServicer.Bootstrap` 与 `Session`，加入鉴权、顺序检查和并行流处理。
2. 注册服务，配置双向消息上限，监听 `127.0.0.1` 的可用端口。
3. 以相同 token、端口、挂载点启动 Go，等待 Notice.ready 后使用挂载路径。
4. 顺序处理 Event，关闭时遵循 Stop/Stopped 流程。

在 `packages/vfspy/` 下运行 `uv run python scripts/generate_proto.py` 可从同一 proto 重新生成两种语言绑定。生成脚本会修正 Python 的相对导入。修改协议后应运行该脚本，确保 Go 与 Python 的服务名、消息类型及字段一致。
