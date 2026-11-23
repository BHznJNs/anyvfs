"""Mount an in-memory filesystem, read and write a file, and print change events."""

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from vfspy import EventValue, InitialDirectory, InitialFile, Mountpoint, VirtualFileSystem


def print_event(event: EventValue) -> None:
    print(f"Event {event.revision}: {type(event.payload).__name__}", flush=True)


async def main(executable: Path | None) -> None:
    with TemporaryDirectory(prefix="vfspy-") as directory:
        mountpoint = Mountpoint(Path(directory) / "mount")
        filesystem = await VirtualFileSystem.create(
            mountpoint,
            entries=[
                InitialFile(path="/docs/hello.txt", data=b"Hello from vfspy!\n"),
                InitialDirectory(path="/inbox"),
            ],
            on_event=print_event,
            executable=executable,
        )
        try:
            hello = mountpoint.path / "docs" / "hello.txt"
            original = await asyncio.to_thread(hello.read_text, encoding="utf-8")
            print(f"Read: {original.rstrip()}", flush=True)

            await asyncio.to_thread(hello.write_text, "Updated from Python.\n", encoding="utf-8")
            updated = await asyncio.to_thread(hello.read_text, encoding="utf-8")
            print(f"Read after writing: {updated.rstrip()}", flush=True)
        finally:
            await filesystem.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="Path to a locally built native bridge")
    asyncio.run(main(parser.parse_args().executable))
