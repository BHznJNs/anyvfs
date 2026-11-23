# vfspy

`vfspy` 是 AnyVFS 的 Python 客户端库，支持 Python 3.11 及以上版本。它管理 Go 原生桥接进程，通过 gRPC 提供挂载配置并接收文件系统变更通知。

```console
pip install vfspy
```

发布的 wheel 自带原生桥接程序。实际挂载需要 Windows 上的 WinFsp 或 Linux 上的 FUSE。
