import asyncio
import unittest

import grpc

from vfspy import EventValue, InitialDirectory, InitialFile
from vfspy._rpc import session_pb2 as pb
from vfspy._rpc import session_pb2_grpc as rpc
from vfspy.server import RpcServer, RpcServerConfig


class RpcServerTests(unittest.IsolatedAsyncioTestCase):
    async def start_server(self, on_event):
        settings = RpcServerConfig(
            case_sensitive=False,
            max_file_bytes=1024,
            max_total_bytes=4096,
            entries=(
                InitialFile(path="/docs/hello.bin", data=b"\x00\xff"),
                InitialDirectory(path="/inbox"),
            ),
            startup_timeout=5,
            stop_timeout=5,
        )
        server = await RpcServer.create(settings, on_event=on_event)
        self.addAsyncCleanup(server.aclose)
        channel = await self.enterAsyncContext(grpc.aio.insecure_channel(server.endpoint))
        metadata = (("authorization", f"Bearer {server.token}"),)
        return server, rpc.ControllerStub(channel), metadata

    async def bootstrap(self, stub, metadata):
        return [item async for item in stub.Bootstrap(pb.Empty(), metadata=metadata, timeout=5)]

    async def open_session(self, on_event):
        server, stub, metadata = await self.start_server(on_event)
        await self.bootstrap(stub, metadata)
        call = stub.Session(metadata=metadata, timeout=15)
        await call.write(pb.Notice(ready=pb.Ready()))
        await server.wait_ready()
        return server, call

    async def test_bootstrap_returns_config_then_entries_in_order(self):
        _, stub, metadata = await self.start_server(lambda event: None)

        self.assertEqual(
            await self.bootstrap(stub, metadata),
            [
                pb.BootstrapItem(
                    config=pb.Config(
                        case_sensitive=False, max_file_bytes=1024, max_total_bytes=4096
                    )
                ),
                pb.BootstrapItem(file=pb.InitialFile(path="/docs/hello.bin", data=b"\x00\xff")),
                pb.BootstrapItem(directory=pb.InitialDirectory(path="/inbox")),
            ],
        )

    async def test_session_delivers_events_to_sync_callback_in_order(self):
        received = []
        _, call = await self.open_session(received.append)
        created = pb.FileCreated(path="/new", mode=0o100644)
        written = pb.FileWritten(path="/new", data=b"hello", size=5, linked=True)

        await call.write(
            pb.Notice(event=pb.Event(inode=2, revision=1, time_ns=100, file_created=created))
        )
        await call.write(
            pb.Notice(event=pb.Event(inode=2, revision=2, time_ns=200, file_written=written))
        )
        await call.write(pb.Notice(stopped=pb.Stopped()))
        await call.done_writing()

        self.assertIs(await call.read(), grpc.aio.EOF)
        self.assertEqual(
            received,
            [
                EventValue(inode=2, revision=1, time_ns=100, payload=created),
                EventValue(inode=2, revision=2, time_ns=200, payload=written),
            ],
        )

    async def test_close_sends_stop_and_waits_for_async_callback(self):
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()
        received = []

        async def on_event(event):
            callback_started.set()
            await release_callback.wait()
            received.append(event)

        server, call = await self.open_session(on_event)
        removed = pb.FileRemoved(path="/docs/hello.bin")

        async with asyncio.TaskGroup() as tasks:
            closing = tasks.create_task(server.aclose())
            self.assertEqual(await call.read(), pb.Command(stop=pb.Stop()))
            await call.write(
                pb.Notice(event=pb.Event(inode=2, revision=1, time_ns=100, file_removed=removed))
            )
            await call.write(pb.Notice(stopped=pb.Stopped()))
            await call.done_writing()
            await asyncio.wait_for(callback_started.wait(), timeout=5)
            self.assertFalse(closing.done())
            release_callback.set()

        self.assertEqual(received, [EventValue(inode=2, revision=1, time_ns=100, payload=removed)])
