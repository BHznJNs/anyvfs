"""Receive change notifications for a single native bridge mount session.

External applications access files through the OS mount path. The bridge reports
committed changes to the control process, where ``on_event`` receives them.

Integration example (the caller manages the bridge subprocess)::

    from asyncio import Event

    from vfspy.server import RpcServer
    from vfspy.server.config import InitialEntry, RpcServerConfig

    settings = RpcServerConfig(
        max_file_bytes=64 << 20,
        max_total_bytes=256 << 20,
        entries=[
            InitialEntry(path="/docs", directory=True),
            InitialEntry(path="/docs/hello.txt", data=b"hello"),
        ],
    )

    async def run_session(shutdown: Event):
        async with RpcServer(settings, on_event=print) as server:
            # Start the bridge subprocess here:
            # pass server.endpoint as --endpoint;
            # pass server.max_message_bytes as --max-message-bytes;
            # pass server.token only through its ANYVFS_TOKEN environment variable.
            await server.wait_ready(timeout=30)
            # Keep listening while external applications use the mount path.
            await shutdown.wait()
        # Wait for the bridge to exit and check its exit code here.

Create, use and close the server on the same asyncio event loop.

Pass the same max_message_bytes value to the bridge's --max-message-bytes option.
Go checks relationships between fields, path components, parent directory order,
duplicate paths and initial data budgets during import. Message size limits
include protobuf encoding overhead.

Synchronous callbacks must not block the event loop. Each event callback must
return before the next notice can be processed. Await wait_closed() or close()
outside the callback.

Closing requests Stop and waits for Stopped. Receiving Stopped completes the
session after all preceding event callbacks have returned. A drain timeout can
leave events undelivered. The ready and stopped flags describe observed history.
Callers must still verify mount readiness and the subprocess exit code.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Self

import grpc

from .._rpc import session_pb2 as pb
from .._rpc import session_pb2_grpc as rpc
from .config import RpcServerConfig
from .errors import ProtocolError, SessionError
from .session import EventHandler, Session


class RpcServer:
    def __init__(self, settings: RpcServerConfig, *, on_event: EventHandler | None = None):
        self._settings = settings
        self._token = secrets.token_urlsafe(32)
        self._session = Session(on_event)
        self._server: grpc.aio.Server | None = None
        self._endpoint: str | None = None
        self._closed = False
        self._closing = False

    @property
    def token(self) -> str:
        return self._token

    @property
    def endpoint(self) -> str:
        if self._endpoint is None:
            raise RuntimeError("server has not started")
        return self._endpoint

    @property
    def max_message_bytes(self) -> int:
        return self._settings.max_message_bytes

    @property
    def revision(self) -> int:
        return self._session.revision

    @property
    def ready(self) -> bool:
        return self._session.ready

    @property
    def stopped(self) -> bool:
        return self._session.stopped

    async def start(self) -> Self:
        if self._closed or self._server is not None:
            raise RuntimeError("server can only be started once")
        server = grpc.aio.server(
            options=(
                ("grpc.max_send_message_length", self.max_message_bytes),
                ("grpc.max_receive_message_length", self.max_message_bytes),
                ("grpc.so_reuseport", 0),
            )
        )
        rpc.add_ControllerServicer_to_server(_ControllerService(self), server)
        try:
            port = server.add_insecure_port("127.0.0.1:0")
            if not port:
                raise RuntimeError("could not bind controller port")
            await server.start()
        except BaseException:
            await server.stop(None)
            raise
        self._server = server
        self._endpoint = f"127.0.0.1:{port}"
        return self

    async def wait_ready(self, timeout: float | None = None):
        self._require_started()
        await self._session.wait_ready(timeout)

    def request_stop(self):
        self._require_started()
        self._session.request_stop()

    async def wait_closed(self, timeout: float | None = None):
        self._require_started()
        await self._session.wait_closed(timeout)

    async def close(self, timeout: float = 10.0):
        if self._closed:
            return
        self._closing = True
        session = self._session
        try:
            if session.started and not session.finished.is_set():
                session.request_stop()
                await session.wait_closed(timeout)
        finally:
            self._closed = True
            session.finish(SessionError("controller closed before session completed"))
            if self._server is not None:
                # Allow the successful Session response to reach the peer.
                await self._server.stop(timeout if session.error is None else None)
        if session.started and session.error is not None:
            raise session.error

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            await self.close()
        except (SessionError, TimeoutError):
            if exc_type is None:
                raise

    def _require_started(self):
        if self._server is None:
            raise RuntimeError("server has not started")


class _ControllerService(rpc.ControllerServicer):
    def __init__(self, owner: RpcServer):
        self._owner = owner
        self._bootstrap_started = False
        self._bootstrap_complete = False

    async def _authorize(self, context: grpc.aio.ServicerContext):
        values = [v for k, v in context.invocation_metadata() if k == "authorization"]
        expected = f"Bearer {self._owner.token}".encode("ascii")
        if len(values) != 1 or not secrets.compare_digest(values[0].encode("utf-8"), expected):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid session token")
        if self._owner._closing:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "controller is closing")

    async def Bootstrap(  # noqa: N802
        self, request: pb.Empty, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.BootstrapItem]:
        await self._authorize(context)
        if self._bootstrap_started:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Bootstrap already consumed")
        self._bootstrap_started = True
        settings = self._owner._settings
        try:
            yield pb.BootstrapItem(
                config=pb.Config(
                    case_sensitive=settings.case_sensitive,
                    max_file_bytes=settings.max_file_bytes,
                    max_total_bytes=settings.max_total_bytes,
                )
            )
            for entry in settings.entries:
                yield pb.BootstrapItem(entry=pb.InitialEntry(**entry.model_dump()))
            self._bootstrap_complete = True
        finally:
            if not self._bootstrap_complete:
                self._owner._session.finish(SessionError("Bootstrap interrupted"))

    async def Session(  # noqa: N802
        self, request_iterator: AsyncIterator[pb.Notice], context: grpc.aio.ServicerContext
    ):
        await self._authorize(context)
        session = self._owner._session
        if not self._bootstrap_complete or session.started or session.finished.is_set():
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Session out of order")
        session.started = True
        receiver = asyncio.create_task(session.receive(request_iterator))
        try:
            while (command := await session.commands.get()) is not None:
                await context.write(command)
            if session.error is not None:
                code = (
                    grpc.StatusCode.INVALID_ARGUMENT
                    if isinstance(session.error, ProtocolError)
                    else grpc.StatusCode.INTERNAL
                )
                await context.abort(code, str(session.error))
        finally:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
            session.finish(SessionError("Session disconnected before Stopped"))
