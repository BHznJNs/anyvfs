from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from .._rpc import session_pb2 as pb
from .errors import SessionError, ProtocolError

EventHandler = Callable[[pb.Event], Awaitable[None] | None]


class Session:
    def __init__(self, on_event: EventHandler | None = None) -> None:
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
        self._next_barrier = 0
        self._barriers: dict[int, asyncio.Event] = {}

    async def wait_ready(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._ready_or_finished.wait(), timeout)
        if not self.ready:
            raise self.error or SessionError("session stopped before Ready")

    async def barrier(self, timeout: float | None = None) -> int:
        self._require_active()
        if not self.ready or self.stop_requested or self.stopped:
            raise RuntimeError("barrier requires a ready session without Stop")
        self._next_barrier += 1
        barrier_id = self._next_barrier
        done = asyncio.Event()
        self._barriers[barrier_id] = done
        self.commands.put_nowait(pb.Command(barrier=pb.Barrier(id=barrier_id)))
        # A timed-out barrier stays registered: its late echo is still valid.
        await asyncio.wait_for(done.wait(), timeout)
        if self.error is not None:
            raise self.error
        return barrier_id

    def request_stop(self) -> None:
        self._require_active()
        if not self.stop_requested and not self.stopped:
            self.stop_requested = True
            self.commands.put_nowait(pb.Command(stop=pb.Stop()))

    async def wait_closed(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self.finished.wait(), timeout)
        if self.error is not None:
            raise self.error

    def _require_active(self) -> None:
        if self.error is not None:
            raise self.error
        if not self.started or self.finished.is_set():
            raise RuntimeError("no active session")

    def finish(self, error: SessionError | None = None) -> None:
        if self.finished.is_set():
            return
        self.error = error
        self.finished.set()
        self._ready_or_finished.set()
        for done in self._barriers.values():
            done.set()
        self._barriers.clear()
        self.commands.put_nowait(None)

    async def receive(self, notices: AsyncIterator[pb.Notice]) -> None:
        try:
            async for notice in notices:
                await self._handle_notice(notice)
            if not self.stopped:
                raise ProtocolError("request stream ended without Stopped")
            if self._barriers:
                raise ProtocolError("request stream ended with unacknowledged barriers")
        except asyncio.CancelledError:
            self.finish(SessionError("notice processing cancelled"))
            raise
        except SessionError as exc:
            self.finish(exc)
        except Exception as exc:
            error = SessionError("notice processing failed")
            error.__cause__ = exc
            self.finish(error)
        else:
            self.finish()

    async def _handle_notice(self, notice: pb.Notice) -> None:
        kind = notice.WhichOneof("value")
        if kind is None or self.stopped:
            raise ProtocolError("empty notice or notice after Stopped")
        if kind == "ready":
            if self.ready:
                raise ProtocolError("duplicate Ready")
            self.ready = True
            self._ready_or_finished.set()
        elif kind == "stopped":
            # Failed mounts may stop without ever reaching Ready.
            self.stopped = True
            self._ready_or_finished.set()
        elif kind == "barrier":
            done = self._barriers.pop(notice.barrier.id, None)
            if done is None:
                raise ProtocolError("unknown or duplicate barrier id")
            done.set()
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
