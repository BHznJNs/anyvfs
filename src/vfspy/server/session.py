from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from .._rpc import session_pb2 as pb
from .errors import ProtocolError, SessionError

EventHandler = Callable[[pb.Event], Awaitable[None] | None]


class Session:
    def __init__(self, on_event: EventHandler | None = None):
        self.on_event = on_event
        self.started = False
        self.ready = False
        self.stopped = False
        self.stop_requested = False
        self.revision = 0
        self.error: SessionError | None = None
        self.commands: asyncio.Queue[pb.Command | None] = asyncio.Queue()
        self.finished = asyncio.Event()
        self._ready_or_finished = asyncio.Event()

    async def wait_ready(self, timeout: float | None = None):
        await asyncio.wait_for(self._ready_or_finished.wait(), timeout)
        if not self.ready:
            raise self.error or SessionError("session stopped before Ready")

    def request_stop(self):
        self._require_active()
        if not self.stop_requested:
            self.stop_requested = True
            self.commands.put_nowait(pb.Command(stop=pb.Stop()))

    async def wait_closed(self, timeout: float | None = None):
        await asyncio.wait_for(self.finished.wait(), timeout)
        if self.error is not None:
            raise self.error

    def _require_active(self):
        if self.error is not None:
            raise self.error
        if not self.started or self.finished.is_set():
            raise RuntimeError("no active session")

    def finish(self, error: SessionError | None = None):
        if self.finished.is_set():
            return
        self.error = error
        self.finished.set()
        self._ready_or_finished.set()
        self.commands.put_nowait(None)

    async def receive(self, notices: AsyncIterator[pb.Notice]):
        try:
            async for notice in notices:
                await self._handle_notice(notice)
                if self.stopped:
                    self.finish()
                    return
            raise ProtocolError("request stream ended without Stopped")
        except asyncio.CancelledError:
            self.finish(SessionError("notice processing cancelled"))
            raise
        except SessionError as exc:
            self.finish(exc)
        except Exception as exc:
            error = SessionError("notice processing failed")
            error.__cause__ = exc
            self.finish(error)

    async def _handle_notice(self, notice: pb.Notice):
        kind = notice.WhichOneof("value")
        if kind is None:
            raise ProtocolError("empty notice")
        if kind == "ready":
            if self.ready:
                raise ProtocolError("duplicate Ready")
            self.ready = True
            self._ready_or_finished.set()
        elif kind == "stopped":
            # Failed mounts may stop without ever reaching Ready.
            self.stopped = True
        elif kind == "event":
            event = notice.event
            if (
                not self.ready
                or event.WhichOneof("kind") is None
                or event.revision != self.revision + 1
                or event.inode == 0
            ):
                raise ProtocolError("invalid event kind, revision, inode or readiness")
            revision = event.revision
            if self.on_event is not None:
                result = self.on_event(event)
                if inspect.isawaitable(result):
                    await result
            self.revision = revision
