# Go 进程管理器

`vfspy.process.BridgeProcess` 只管理一个 Go bridge 子进程。
不创建控制服务、不判断挂载就绪、不管理会话，也不包含调度、进程池或自动重启。
创建、使用和关闭须在同一个 asyncio 事件循环中完成；实例只能启动一次。

## 创建与启动

```python
from vfspy.process import BridgeProcess, BridgeProcessConfig

# server 由调用方创建并启动。
settings = BridgeProcessConfig(
    mountpoint="Z:",  # Linux 下使用挂载目录，例如 /mnt/vfspy。
    endpoint=server.endpoint,
    token=server.token,
    max_message_bytes=server.max_message_bytes,
    # wheel 默认使用包内程序；editable 开发环境需指定构建产物：
    # executable="D:/Projects/vfspy/vfspy_v3/bin/vfspy-native.exe",
    startup_timeout=25,
    stop_timeout=5,
)
process = BridgeProcess(settings)

await process.start()
try:
    await server.wait_ready(timeout=30)
    # 此处使用挂载路径。
    # 正常关闭由调用方先结束 RPC 会话，排空通知，再检查进程退出。
    await server.close(timeout=10)
    await process.wait(timeout=10)
finally:
    await process.close()
```

`start()` 返回只表示子进程创建成功；实际挂载状态由 RPC 的 `Ready` 判断。
`startup_timeout` 按秒配置，向上取整到毫秒后传给 Go 的 `--startup-timeout`，只限制 Bootstrap，
不作为 Python 侧的挂载或进程退出超时。`stop_timeout` 是 `terminate()` / `close()`
发出终止请求后，升级为强杀前的等待秒数。

`BridgeProcessConfig` 是不可变的 Pydantic 模型，拒绝未知字段；参数类型、端口范围、
消息大小、正数且有限的超时，以及字符串中的 NUL 均在模型创建时校验，失败抛出
`pydantic.ValidationError`。`token` 不出现在模型 repr 中。

默认从 wheel 的 `vfspy/_bin/vfspy-native[.exe]` 定位程序；缺失时给出构建和
配置 `executable` 的提示。显式路径转为绝对路径，启动不经过 shell。Windows 使用隐藏窗口。
`VFSPY_TOKEN` 只写入子进程环境，覆盖子进程继承的同名变量，不修改父进程环境，
也不出现在命令行中。

## 生命周期接口

| 接口 | 行为 |
| --- | --- |
| `await start()` | 启动并返回当前实例；重复启动或关闭后启动报 `RuntimeError`。启动失败或启动等待被取消时回收资源。 |
| `await wait(timeout=None, check=True)` | 等待进程退出和 stderr 读取完成，返回退出码；默认对非零退出抛出 `BridgeProcessError`。超时或取消只结束当前等待，不终止子进程。支持多个等待者。 |
| `await terminate(timeout=None)` | 请求系统终止；超时后升级为 kill 并回收进程。返回退出码，不对主动终止的非零退出报错。 |
| `await kill()` | 立即强制终止并回收，返回退出码。 |
| `await close()` | 幂等关闭；即使当前等待被取消，也先完成终止和回收。关闭与启动并发时同样回收子进程。未启动时也可关闭。 |
| `async with process` | 进入时 `start()`，退出时 `close()`。 |

`pid` 和 `returncode` 在未启动时为 `None`；`running` 表示已创建且尚未观察到退出。
`stderr` 可随时读取，按 UTF-8 解码，非法字节替换为 `�`。后台持续读取 stderr，
仅保留最后 64 KiB 原始字节，避免输出堵塞子进程或日志无限占用内存。stdin 和 stdout
接入空设备；Go 没有基于 stdout 的业务协议。

`BridgeProcessError` 提供 `returncode`、`stderr`，异常文本包含最后 4096 个诊断字符。
`wait(check=False)` 可直接取得失败退出码。只检查退出码不等于验证 RPC 会话成功。

Windows 的系统 terminate 本身就是强制终止；其他平台使用 SIGTERM，超时后使用 SIGKILL。
`close()` 是进程回收兜底，不能保证卸载成功或末尾事件交付。需要完整事件时，调用方应先按
[RPC 生命周期](rpc.md) 发送 Stop、处理 Stopped 和流 EOF，再等待进程退出。
若使用异步上下文，同样应在退出进程上下文前完成这个顺序。

## 模块结构

- `vfspy.process.config`：`BridgeProcessConfig` 与参数约束。
- `vfspy.process.errors`：`BridgeProcessError`。
- `vfspy.process.manager`：`BridgeProcess` 的进程生命周期实现。
- `vfspy.process`：统一导出以上三个公共类。
