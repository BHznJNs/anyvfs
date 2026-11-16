import asyncio
import unittest
from contextlib import suppress

import grpc

from vfspy._rpc import session_pb2 as pb
from vfspy._rpc import session_pb2_grpc as rpc
from vfspy.server import RpcServer
from vfspy.server.config import InitialEntry, RpcServerConfig
from vfspy.server.errors import SessionError


def settings(**overrides):
    return RpcServerConfig(
        **{"case_sensitive": True, "max_file_bytes": 1024, "max_total_bytes": 4096, **overrides}
    )


def event(revision=1):
    return pb.Notice(
        event=pb.Event(
            revision=revision, inode=2, file_created=pb.FileCreated(path="/new", mode=33188)
        )
    )


class RpcServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.server = await RpcServer(
            settings(entries=[InitialEntry(path="/hello", data=b"hello")]),
            on_event=self.events.append,
        ).start()
        self.channel = grpc.aio.insecure_channel(self.server.endpoint)
        self.stub = rpc.ControllerStub(self.channel)
        self.metadata = (("authorization", f"Bearer {self.server.token}"),)

    async def asyncTearDown(self):
        await self.channel.close()
        with suppress(SessionError, TimeoutError):
            await self.server.close(timeout=0.2)

    async def bootstrap(self):
        return [
            item
            async for item in self.stub.Bootstrap(pb.Empty(), metadata=self.metadata, timeout=2)
        ]

    async def session(self, ready=True):
        await self.bootstrap()
        call = self.stub.Session(metadata=self.metadata, timeout=5)
        if ready:
            await call.write(pb.Notice(ready=pb.Ready()))
            await self.server.wait_ready(timeout=2)
        return call

    async def finish(self, call):
        await call.write(pb.Notice(stopped=pb.Stopped()))
        await call.done_writing()
        self.assertIs(await call.read(), grpc.aio.EOF)
        await self.server.wait_closed(timeout=2)

    async def test_bootstrap_order_and_single_use(self):
        items = await self.bootstrap()
        self.assertEqual([item.WhichOneof("value") for item in items], ["config", "entry"])
        self.assertEqual(
            items[0].config,
            pb.Config(case_sensitive=True, max_file_bytes=1024, max_total_bytes=4096),
        )
        self.assertEqual(items[1].entry.data, b"hello")
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await self.bootstrap()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)

    async def test_authentication_both_methods_and_rejected_calls_do_not_consume(self):
        for metadata in ((), (("authorization", "Bearer wrong"),), self.metadata * 2):
            with self.subTest(metadata=metadata):
                with self.assertRaises(grpc.aio.AioRpcError) as raised:
                    await self.stub.Bootstrap(pb.Empty(), metadata=metadata, timeout=2).read()
                self.assertEqual(raised.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
                with self.assertRaises(grpc.aio.AioRpcError) as raised:
                    await self.stub.Session(metadata=metadata, timeout=2).read()
                self.assertEqual(raised.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
        call = await self.session()
        await self.finish(call)

    async def test_session_order_and_duplicate(self):
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await self.stub.Session(metadata=self.metadata, timeout=2).read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        call = await self.session()
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await self.stub.Session(metadata=self.metadata, timeout=2).read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        await self.finish(call)

    async def test_barrier_waits_for_callback_and_can_send_while_callback_waits(self):
        entered, release = (asyncio.Event(), asyncio.Event())

        async def callback(item):
            entered.set()
            await release.wait()
            self.events.append(item)

        self.server._session.on_event = callback
        call = await self.session()
        await call.write(event())
        await asyncio.wait_for(entered.wait(), 2)
        barrier = asyncio.create_task(self.server.barrier(timeout=2))
        command = await call.read()
        self.assertEqual(command.WhichOneof("value"), "barrier")
        await call.write(pb.Notice(barrier=command.barrier))
        self.assertFalse(barrier.done())
        self.assertEqual(self.server.revision, 0)
        release.set()
        self.assertEqual(await barrier, command.barrier.id)
        self.assertEqual(self.server.revision, 1)
        self.assertEqual(len(self.events), 1)
        await self.finish(call)

    async def test_late_barrier_reply_after_timeout_is_valid(self):
        call = await self.session()
        barrier = asyncio.create_task(self.server.barrier(timeout=0.05))
        command = await call.read()
        with self.assertRaises(TimeoutError):
            await barrier
        await call.write(pb.Notice(barrier=command.barrier))
        second = asyncio.create_task(self.server.barrier(timeout=2))
        command2 = await call.read()
        self.assertGreater(command2.barrier.id, command.barrier.id)
        await call.write(pb.Notice(barrier=command2.barrier))
        await second
        await self.finish(call)

    async def test_close_sends_stop_once_and_drains_final_event(self):
        call = await self.session()
        closing = asyncio.create_task(self.server.close(timeout=2))
        command = await call.read()
        self.assertEqual(command.WhichOneof("value"), "stop")
        self.server.request_stop()
        self.assertFalse(closing.done())
        await call.write(event())
        await self.finish(call)
        await closing
        self.assertEqual(self.server.revision, 1)
        await self.server.close()

    async def test_stopped_without_ready_does_not_claim_mount_success(self):
        call = await self.session(ready=False)
        await self.finish(call)
        with self.assertRaisesRegex(SessionError, "before Ready"):
            await self.server.wait_ready(timeout=2)
        self.assertFalse(self.server.ready)
        self.assertTrue(self.server.stopped)

    async def test_empty_notice_rejected(self):
        call = await self.session()
        await call.write(pb.Notice())
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(SessionError):
            await self.server.wait_closed(timeout=2)

    async def test_duplicate_ready_rejected(self):
        call = await self.session()
        await call.write(pb.Notice(ready=pb.Ready()))
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_unknown_barrier_rejected(self):
        call = await self.session()
        await call.write(pb.Notice(barrier=pb.Barrier(id=123)))
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_notice_after_stopped_rejected(self):
        call = await self.session()
        await call.write(pb.Notice(stopped=pb.Stopped()))
        await call.write(event())
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_stopped_unblocks_wait_ready_before_eof(self):
        call = await self.session(ready=False)
        await call.write(pb.Notice(stopped=pb.Stopped()))
        with self.assertRaisesRegex(SessionError, "before Ready"):
            await self.server.wait_ready(timeout=2)
        await call.done_writing()
        self.assertIs(await call.read(), grpc.aio.EOF)

    async def test_missing_barrier_echo_rejected_at_eof(self):
        call = await self.session()
        barrier = asyncio.create_task(self.server.barrier(timeout=2))
        await call.read()
        await call.write(pb.Notice(stopped=pb.Stopped()))
        await call.done_writing()
        with self.assertRaisesRegex(SessionError, "unacknowledged"):
            await barrier
        with self.assertRaises(grpc.aio.AioRpcError):
            await call.read()

    async def test_invalid_events_rejected(self):
        invalid = [
            event(2),
            pb.Notice(event=pb.Event(revision=1, inode=2)),
            pb.Notice(event=pb.Event(revision=1, file_removed=pb.FileRemoved(path="/x"))),
        ]
        for notice in invalid:
            with self.subTest(notice=notice):
                server = await RpcServer(settings()).start()
                async with grpc.aio.insecure_channel(server.endpoint) as channel:
                    stub = rpc.ControllerStub(channel)
                    metadata = (("authorization", f"Bearer {server.token}"),)
                    _ = [s async for s in stub.Bootstrap(pb.Empty(), metadata=metadata)]
                    call = stub.Session(metadata=metadata, timeout=2)
                    await call.write(pb.Notice(ready=pb.Ready()))
                    await call.write(notice)
                    with self.assertRaises(grpc.aio.AioRpcError) as raised:
                        await call.read()
                    self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
                with self.assertRaises(SessionError):
                    await server.close()

    async def test_event_before_ready_rejected_and_wait_ready_unblocked(self):
        call = await self.session(ready=False)
        await call.write(event())
        with self.assertRaises(SessionError):
            await self.server.wait_ready(timeout=2)
        with self.assertRaises(grpc.aio.AioRpcError):
            await call.read()

    async def test_eof_without_stopped_unblocks_barrier(self):
        call = await self.session()
        barrier = asyncio.create_task(self.server.barrier(timeout=2))
        await call.read()
        await call.done_writing()
        with self.assertRaises(SessionError):
            await barrier
        with self.assertRaises(grpc.aio.AioRpcError):
            await call.read()

    async def test_disconnect_unblocks_waiters(self):
        call = await self.session()
        barrier = asyncio.create_task(self.server.barrier(timeout=2))
        await call.read()
        call.cancel()
        with self.assertRaises(SessionError):
            await barrier
        with self.assertRaises(SessionError):
            await self.server.wait_closed(timeout=2)

    async def test_callback_failure_is_reported(self):

        def callback(item):
            raise ValueError("callback failed")

        self.server._session.on_event = callback
        call = await self.session()
        await call.write(event())
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INTERNAL)
        with self.assertRaises(SessionError) as raised:
            await self.server.wait_closed(timeout=2)
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertEqual(self.server.revision, 0)

    async def test_cancelled_callback_unblocks_session(self):

        async def callback(item):
            raise asyncio.CancelledError

        self.server._session.on_event = callback
        call = await self.session()
        await call.write(event())
        with self.assertRaises(SessionError):
            await self.server.wait_closed(timeout=2)
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INTERNAL)

    async def test_runtime_receive_message_limit(self):
        server = await RpcServer(settings(max_message_bytes=64)).start()
        async with grpc.aio.insecure_channel(server.endpoint) as channel:
            stub = rpc.ControllerStub(channel)
            metadata = (("authorization", f"Bearer {server.token}"),)
            _ = [s async for s in stub.Bootstrap(pb.Empty(), metadata=metadata)]
            call = stub.Session(metadata=metadata, timeout=2)
            await call.write(pb.Notice(ready=pb.Ready()))
            await server.wait_ready(timeout=2)
            await call.write(
                pb.Notice(
                    event=pb.Event(
                        revision=1,
                        inode=2,
                        file_written=pb.FileWritten(path="/hello", data=b"x" * 100),
                    )
                )
            )
            with self.assertRaises(grpc.aio.AioRpcError) as raised:
                await call.read()
            self.assertEqual(raised.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
        with self.assertRaises(SessionError):
            await server.close(timeout=0.2)

    async def test_close_timeout_still_releases_server(self):
        call = await self.session()
        closing = asyncio.create_task(self.server.close(timeout=0.05))
        self.assertEqual((await call.read()).WhichOneof("value"), "stop")
        with self.assertRaises(TimeoutError):
            await closing
        self.assertIsNot(await call.code(), grpc.StatusCode.OK)
        await self.server.close()

    async def test_context_manager_without_client(self):
        async with RpcServer(settings()) as server:
            self.assertTrue(server.endpoint.startswith("127.0.0.1:"))
        with self.assertRaises(RuntimeError):
            await server.start()

    async def test_generated_ports_and_tokens_are_distinct(self):
        async with RpcServer(settings()) as other:
            self.assertNotEqual(self.server.endpoint, other.endpoint)
            self.assertNotEqual(self.server.token, other.token)

    async def test_bootstrap_preserves_nested_entries_and_binary_data(self):
        config = settings(
            case_sensitive=False,
            max_file_bytes=2048,
            max_total_bytes=8192,
            max_message_bytes=128,
            entries=[
                InitialEntry(path="/docs", directory=True),
                InitialEntry(path="/docs/你好.bin", data=b"\x00\xff\x80"),
            ],
        )
        async with RpcServer(config) as server:
            self.assertEqual(server.max_message_bytes, 128)
            async with grpc.aio.insecure_channel(server.endpoint) as channel:
                items = [
                    item
                    async for item in rpc.ControllerStub(channel).Bootstrap(
                        pb.Empty(),
                        metadata=(("authorization", f"Bearer {server.token}"),),
                        timeout=2,
                    )
                ]
        self.assertEqual(
            items[0].config,
            pb.Config(case_sensitive=False, max_file_bytes=2048, max_total_bytes=8192),
        )
        self.assertTrue(items[1].entry.directory)
        self.assertEqual(items[2].entry.path, "/docs/你好.bin")
        self.assertEqual(items[2].entry.data, b"\x00\xff\x80")

    async def test_bootstrap_send_message_limit(self):
        config = settings(
            entries=[InitialEntry(path="/large", data=b"x" * 100)],
            max_message_bytes=64,
        )
        async with RpcServer(config) as server:
            async with grpc.aio.insecure_channel(server.endpoint) as channel:
                call = rpc.ControllerStub(channel).Bootstrap(
                    pb.Empty(), metadata=(("authorization", f"Bearer {server.token}"),), timeout=2
                )
                self.assertEqual((await call.read()).WhichOneof("value"), "config")
                with self.assertRaises(grpc.aio.AioRpcError) as raised:
                    await call.read()
                self.assertEqual(raised.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)

    async def test_close_before_start_prevents_start(self):
        server = RpcServer(settings())
        await server.close()
        await server.close()
        with self.assertRaises(RuntimeError):
            await server.start()
