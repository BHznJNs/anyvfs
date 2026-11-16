from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Config(_message.Message):
    __slots__ = ("case_sensitive", "max_file_bytes", "max_total_bytes")
    CASE_SENSITIVE_FIELD_NUMBER: _ClassVar[int]
    MAX_FILE_BYTES_FIELD_NUMBER: _ClassVar[int]
    MAX_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    case_sensitive: bool
    max_file_bytes: int
    max_total_bytes: int
    def __init__(self, case_sensitive: bool = ..., max_file_bytes: _Optional[int] = ..., max_total_bytes: _Optional[int] = ...) -> None: ...

class InitialEntry(_message.Message):
    __slots__ = ("path", "directory", "data")
    PATH_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    path: str
    directory: bool
    data: bytes
    def __init__(self, path: _Optional[str] = ..., directory: bool = ..., data: _Optional[bytes] = ...) -> None: ...

class Seed(_message.Message):
    __slots__ = ("config", "entry")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    ENTRY_FIELD_NUMBER: _ClassVar[int]
    config: Config
    entry: InitialEntry
    def __init__(self, config: _Optional[_Union[Config, _Mapping]] = ..., entry: _Optional[_Union[InitialEntry, _Mapping]] = ...) -> None: ...

class Ready(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Stopped(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Barrier(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    def __init__(self, id: _Optional[int] = ...) -> None: ...

class Stop(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Command(_message.Message):
    __slots__ = ("stop", "barrier")
    STOP_FIELD_NUMBER: _ClassVar[int]
    BARRIER_FIELD_NUMBER: _ClassVar[int]
    stop: Stop
    barrier: Barrier
    def __init__(self, stop: _Optional[_Union[Stop, _Mapping]] = ..., barrier: _Optional[_Union[Barrier, _Mapping]] = ...) -> None: ...

class Notice(_message.Message):
    __slots__ = ("ready", "event", "barrier", "stopped")
    READY_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    BARRIER_FIELD_NUMBER: _ClassVar[int]
    STOPPED_FIELD_NUMBER: _ClassVar[int]
    ready: Ready
    event: Event
    barrier: Barrier
    stopped: Stopped
    def __init__(self, ready: _Optional[_Union[Ready, _Mapping]] = ..., event: _Optional[_Union[Event, _Mapping]] = ..., barrier: _Optional[_Union[Barrier, _Mapping]] = ..., stopped: _Optional[_Union[Stopped, _Mapping]] = ...) -> None: ...

class FileCreated(_message.Message):
    __slots__ = ("path", "mode")
    PATH_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    path: str
    mode: int
    def __init__(self, path: _Optional[str] = ..., mode: _Optional[int] = ...) -> None: ...

class DirectoryCreated(_message.Message):
    __slots__ = ("path", "mode")
    PATH_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    path: str
    mode: int
    def __init__(self, path: _Optional[str] = ..., mode: _Optional[int] = ...) -> None: ...

class FileWritten(_message.Message):
    __slots__ = ("path", "offset", "data", "size", "linked")
    PATH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    LINKED_FIELD_NUMBER: _ClassVar[int]
    path: str
    offset: int
    data: bytes
    size: int
    linked: bool
    def __init__(self, path: _Optional[str] = ..., offset: _Optional[int] = ..., data: _Optional[bytes] = ..., size: _Optional[int] = ..., linked: bool = ...) -> None: ...

class FileTruncated(_message.Message):
    __slots__ = ("path", "previous_size", "size", "linked")
    PATH_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_SIZE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    LINKED_FIELD_NUMBER: _ClassVar[int]
    path: str
    previous_size: int
    size: int
    linked: bool
    def __init__(self, path: _Optional[str] = ..., previous_size: _Optional[int] = ..., size: _Optional[int] = ..., linked: bool = ...) -> None: ...

class EntryRenamed(_message.Message):
    __slots__ = ("path", "new_path", "directory", "replaced_inode")
    PATH_FIELD_NUMBER: _ClassVar[int]
    NEW_PATH_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_FIELD_NUMBER: _ClassVar[int]
    REPLACED_INODE_FIELD_NUMBER: _ClassVar[int]
    path: str
    new_path: str
    directory: bool
    replaced_inode: int
    def __init__(self, path: _Optional[str] = ..., new_path: _Optional[str] = ..., directory: bool = ..., replaced_inode: _Optional[int] = ...) -> None: ...

class FileRemoved(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class DirectoryRemoved(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class ModeChanged(_message.Message):
    __slots__ = ("path", "mode")
    PATH_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    path: str
    mode: int
    def __init__(self, path: _Optional[str] = ..., mode: _Optional[int] = ...) -> None: ...

class OwnerChanged(_message.Message):
    __slots__ = ("path", "uid", "gid")
    PATH_FIELD_NUMBER: _ClassVar[int]
    UID_FIELD_NUMBER: _ClassVar[int]
    GID_FIELD_NUMBER: _ClassVar[int]
    path: str
    uid: int
    gid: int
    def __init__(self, path: _Optional[str] = ..., uid: _Optional[int] = ..., gid: _Optional[int] = ...) -> None: ...

class TimesChanged(_message.Message):
    __slots__ = ("path", "atime_ns", "mtime_ns")
    PATH_FIELD_NUMBER: _ClassVar[int]
    ATIME_NS_FIELD_NUMBER: _ClassVar[int]
    MTIME_NS_FIELD_NUMBER: _ClassVar[int]
    path: str
    atime_ns: int
    mtime_ns: int
    def __init__(self, path: _Optional[str] = ..., atime_ns: _Optional[int] = ..., mtime_ns: _Optional[int] = ...) -> None: ...

class Event(_message.Message):
    __slots__ = ("revision", "time_ns", "inode", "file_created", "directory_created", "file_written", "file_truncated", "entry_renamed", "file_removed", "directory_removed", "mode_changed", "owner_changed", "times_changed")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    TIME_NS_FIELD_NUMBER: _ClassVar[int]
    INODE_FIELD_NUMBER: _ClassVar[int]
    FILE_CREATED_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_CREATED_FIELD_NUMBER: _ClassVar[int]
    FILE_WRITTEN_FIELD_NUMBER: _ClassVar[int]
    FILE_TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    ENTRY_RENAMED_FIELD_NUMBER: _ClassVar[int]
    FILE_REMOVED_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_REMOVED_FIELD_NUMBER: _ClassVar[int]
    MODE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    OWNER_CHANGED_FIELD_NUMBER: _ClassVar[int]
    TIMES_CHANGED_FIELD_NUMBER: _ClassVar[int]
    revision: int
    time_ns: int
    inode: int
    file_created: FileCreated
    directory_created: DirectoryCreated
    file_written: FileWritten
    file_truncated: FileTruncated
    entry_renamed: EntryRenamed
    file_removed: FileRemoved
    directory_removed: DirectoryRemoved
    mode_changed: ModeChanged
    owner_changed: OwnerChanged
    times_changed: TimesChanged
    def __init__(self, revision: _Optional[int] = ..., time_ns: _Optional[int] = ..., inode: _Optional[int] = ..., file_created: _Optional[_Union[FileCreated, _Mapping]] = ..., directory_created: _Optional[_Union[DirectoryCreated, _Mapping]] = ..., file_written: _Optional[_Union[FileWritten, _Mapping]] = ..., file_truncated: _Optional[_Union[FileTruncated, _Mapping]] = ..., entry_renamed: _Optional[_Union[EntryRenamed, _Mapping]] = ..., file_removed: _Optional[_Union[FileRemoved, _Mapping]] = ..., directory_removed: _Optional[_Union[DirectoryRemoved, _Mapping]] = ..., mode_changed: _Optional[_Union[ModeChanged, _Mapping]] = ..., owner_changed: _Optional[_Union[OwnerChanged, _Mapping]] = ..., times_changed: _Optional[_Union[TimesChanged, _Mapping]] = ...) -> None: ...
