import asyncio
import secrets
from collections.abc import AsyncIterator
from typing import Awaitable, Callable

import grpc

from .._rpc import session_pb2 as pb
from .._rpc import session_pb2_grpc as rpc
from ..utils.awaitable_or import AwaitableOr, resolve_awaitable_or
from .config import RpcServerConfig
from .errors import ProtocolError, SessionError
from .events import EventValue


EventHandler = Callable[[EventValue], AwaitableOr[None]]


class SessionLifecycle:
    def __init__(self,
                 startup: asyncio.Future[None],
                 on_event: EventHandler,
                 send_stop: Callable[[], Awaitable[None]]):
        self._revision = 0
        self._on_event = on_event
        self._send_stop = send_stop
        self._startup = startup
        self._stopped_event = asyncio.Event()

    @property
    def stopped(self) -> asyncio.Event:
        return self._stopped_event

    async def receive(self, notices: AsyncIterator[pb.Notice]):
        try:
            async for notice in notices:
                await self._handle_notice(notice)
                if self._stopped_event.is_set(): return
            raise ProtocolError("Session request stream ended before Stopped")
        finally:
            if not self._startup.done():
                self._startup.set_exception(SessionError("Session ended before Ready"))

    async def _handle_notice(self, notice: pb.Notice):
        kind = notice.WhichOneof("value")
        match kind:
            case None:
                raise ProtocolError("empty notice")
            case "ready":
                if self._startup.done():
                    raise ProtocolError("duplicate Ready")
                self._startup.set_result(None)
            case "stopped":
                if not self._startup.done():
                    self._startup.set_exception(SessionError("Session not started before ready"))
                self._stopped_event.set()
            case "event":
                event = notice.event
                revision = event.revision
                if revision != self._revision + 1:
                    raise ProtocolError("invalid event revision")

                event_value = EventValue.from_pb_event(event)
                await resolve_awaitable_or(self._on_event(event_value))
                self._revision = revision

    async def stop(self):
        await self._send_stop()

class RpcController(rpc.ControllerServicer):
    def __init__(self, token: str, settings: RpcServerConfig, on_event: EventHandler):
        self._token = token
        self._settings = settings
        self._on_event = on_event
        self._bootstrapped = False
        self._ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._session: SessionLifecycle | None = None

    async def _authorize(self, context: grpc.aio.ServicerContext):
        metadata = context.invocation_metadata() or ()
        auth_values = [value for key, value in metadata if key == "authorization"]
        expected = f"Bearer {self._token}"
        if (
            len(auth_values) != 1
            or not isinstance(auth_values[0], str)
            or not auth_values[0].isascii()
            or not secrets.compare_digest(auth_values[0], expected)
        ):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid session token")

    async def Bootstrap(  # noqa: N802
        self, request: pb.Empty, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.BootstrapItem]:
        await self._authorize(context)
        if self._bootstrapped:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Bootstrap already consumed")
        self._bootstrapped = True

        settings = self._settings
        yield pb.BootstrapItem(
            config=pb.Config(
                case_sensitive=settings.case_sensitive,
                max_file_bytes=settings.max_file_bytes,
                max_total_bytes=settings.max_total_bytes,
            )
        )
        for entry in settings.entries:
            yield pb.BootstrapItem(entry=pb.InitialEntry(**entry.model_dump()))

    async def Session(  # noqa: N802
        self, request_iterator: AsyncIterator[pb.Notice], context: grpc.aio.ServicerContext
    ):
        await self._authorize(context)
        if not self._bootstrapped:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Bootstrap not consumed")
        if self._session is not None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Only one session allowed")
        self._session = SessionLifecycle(
            self._ready,
            self._on_event,
            lambda: context.write(pb.Command(stop=pb.Stop())))

        try:
            await self._session.receive(request_iterator)
        except ProtocolError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

    async def wait_ready(self, timeout: float | None = None):
        """
        Raises:
            asyncio.TimeoutError: if the session does not become ready within the timeout
        """
        async with asyncio.timeout(timeout):
            await asyncio.shield(self._ready)

    async def stop(self, timeout: float | None = None):
        """
        Raises:
            asyncio.TimeoutError: if the session does not stop within the timeout
        """
        session = self._session
        if session is None:
            if not self._ready.done():
                self._ready.set_exception(SessionError("Controller closed before Ready"))
                self._ready.exception()
            return
        if session.stopped.is_set(): return

        self._session = None
        async with asyncio.timeout(timeout):
            await session.stop()
            await session.stopped.wait()
