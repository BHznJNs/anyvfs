# vfspy

vfspy 是基于 Go 内存文件系统与 gRPC 控制协议的项目。Python 包名为 `vfspy`，Go 模块名为 `vfspy/native`。

Go 负责文件系统状态与 WinFsp/FUSE 挂载；控制进程通过 gRPC 提供初始化数据、接收变更事件并管理会话。Python 包提供 `vfspy._rpc` 生成绑定，控制服务由应用实现。

## 文档

- [开发文档](docs/development.md)
- [go 桥接库文档](native/README.md)
