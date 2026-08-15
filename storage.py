"""Atomic file writes — never leave a half-written state file behind on a crash.

An unattended process can die mid-write: an OOM kill on a small VM, a deploy
restart, a stray signal. A plain open(path, "w") TRUNCATES the file before
writing, so a crash at the wrong instant leaves a corrupt or empty JSON that
breaks the *next* run — exactly when you're not watching.

The fix is the write-temp-then-rename dance:
  1. write the complete new content to a temp file in the SAME directory,
  2. flush + fsync it to disk,
  3. os.replace() it over the target.

os.replace() is an atomic rename on the same filesystem (POSIX and Windows), so
any reader always sees either the whole old file or the whole new one — never a
partial write. Keeping the temp file in the same directory is what guarantees the
rename stays on one filesystem (a cross-device rename is not atomic).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_text_atomic(path, text: str) -> None:
    """Atomically replace `path`'s contents with `text`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # force the bytes to disk before the rename
        os.replace(tmp, path)  # atomic
    except BaseException:
        # Something failed before the rename — remove the temp file so we don't
        # litter, and let the original error propagate. The real file is untouched.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, obj, indent: int = 2) -> None:
    """Atomically write `obj` to `path` as pretty JSON with a trailing newline."""
    write_text_atomic(path, json.dumps(obj, indent=indent, default=str) + "\n")
