"""File and byte-size helper utilities."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")


def atomic_write_text(
    path: str | Path,
    data: str,
    *,
    encoding: str = "utf-8",
    errors: str | None = None,
    prefix: str = ".tmp_",
    suffix: str = ".tmp",
) -> None:
    """Atomically write text by replacing the target with a same-directory temp file."""
    target = Path(path)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=suffix, prefix=prefix)
    try:
        with os.fdopen(fd, "w", encoding=encoding, errors=errors) as f:
            f.write(data)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def format_bytes(size: int | float) -> str:
    """Format a byte count using binary units."""
    value = float(max(0, size))
    for unit in _BYTE_UNITS:
        if value < 1024 or unit == _BYTE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"
