"""Small cross-platform advisory lock for Hub-owned local state files."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


_thread_locks_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Lock one byte of a Hub-owned lock file on POSIX or Windows.

    The lock file deliberately retains its one-byte marker on Windows.  It is
    runtime state, not user data, and keeps ``msvcrt.locking`` well-defined.
    """

    lock = _thread_lock(path)
    with lock:
        if os.name == "nt":
            _ensure_windows_marker(path)
            mode = "r+"
        else:
            mode = "a+"
        with path.open(mode, encoding="utf-8") as stream:
            _acquire(stream)
            try:
                yield
            finally:
                _release(stream)


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


def _ensure_windows_marker(path: Path) -> None:
    """Create the byte msvcrt locks, without reading another holder's byte."""

    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write("0")
    except FileExistsError:
        pass


def _acquire(stream: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _release(stream: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
