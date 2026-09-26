"""Small shared boundaries for managed paths and durable directory changes."""
import os
from pathlib import Path

from .pack import GameStackError


def safe_child(parent: Path, component: str) -> Path:
    path = parent / component
    if (not component or component in (".", "..") or Path(component).name != component or
            any(p.is_symlink() for p in (path, *path.parents)) or
            path.resolve().parent != parent.resolve()):
        raise GameStackError("A managed path is unsafe. Use a direct child of the GameStack folder without symbolic links or traversal.")
    return path


def sync_directory(directory: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
