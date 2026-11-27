from dataclasses import dataclass
from typing import Self, TypeAlias, cast

from .._rpc import events_pb2 as pb
from .errors import ProtocolError

EventPayload: TypeAlias = (
    pb.FileCreated
    | pb.DirectoryCreated
    | pb.FileWritten
    | pb.FileTruncated
    | pb.EntryRenamed
    | pb.FileRemoved
    | pb.DirectoryRemoved
    | pb.ModeChanged
    | pb.OwnerChanged
    | pb.TimesChanged
)


@dataclass(frozen=True, slots=True)
class EventValue:
    inode: int
    revision: int
    time_ns: int
    payload: EventPayload

    @classmethod
    def from_pb_event(cls, event: pb.Event) -> Self:
        """Return the validated event metadata and its selected payload."""
        kind = event.WhichOneof("kind")
        if kind is None:
            raise ProtocolError("empty event")
        if event.inode == 0:
            raise ProtocolError("invalid event inode")
        return cls(
            inode=event.inode,
            revision=event.revision,
            time_ns=event.time_ns,
            payload=cast(EventPayload, getattr(event, kind)),
        )
