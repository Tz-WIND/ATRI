"""Persistence and archive management for Music Studio projects."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from io import BufferedRandom
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from core.utils import atomic_write_text

if os.name == "nt":
    import msvcrt

PROJECT_PATH = Path("data/music_workstation/project.json")
PROJECTS_DIR = Path("data/music_workstation/projects")
PROJECT_INDEX_PATH = Path("data/music_workstation/project_index.json")
PROJECT_LOCK_PATH = Path("data/music_workstation/project.lock")
_PROJECT_LOCK_BYTES = 1
_PROJECT_THREAD_LOCK = threading.RLock()


class _ProjectModel(Protocol):
    def default_project(self) -> dict[str, Any]: ...

    def normalize_project(self, project: dict[str, Any] | None) -> dict[str, Any]: ...

    def project_summary(self, project: dict[str, Any]) -> dict[str, Any]: ...


def _project_model() -> _ProjectModel:
    from core.music import project_model

    return cast(_ProjectModel, project_model)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def _project_storage_lock(path: Path) -> Iterator[None]:
    lock_path = PROJECT_LOCK_PATH if path == PROJECT_PATH else path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROJECT_THREAD_LOCK:
        with lock_path.open("a+b") as lock_file:
            _lock_file(lock_file)
            try:
                yield
            finally:
                _unlock_file(lock_file)


def _lock_file(lock_file: BufferedRandom) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, _PROJECT_LOCK_BYTES)
    else:
        posix_fcntl = _posix_fcntl()
        posix_fcntl.flock(lock_file.fileno(), posix_fcntl.LOCK_EX)


def _unlock_file(lock_file: BufferedRandom) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, _PROJECT_LOCK_BYTES)
    else:
        posix_fcntl = _posix_fcntl()
        posix_fcntl.flock(lock_file.fileno(), posix_fcntl.LOCK_UN)


def _posix_fcntl() -> Any:
    return __import__("fcntl")


def load_project(path: Path | str = PROJECT_PATH) -> dict[str, Any]:
    project_path = Path(path)
    with _project_storage_lock(project_path):
        return _load_project_unlocked(project_path)


def save_project(project: dict[str, Any], path: Path | str = PROJECT_PATH) -> dict[str, Any]:
    project_path = Path(path)
    with _project_storage_lock(project_path):
        return _save_project_unlocked(project, project_path)


def update_project(
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    path: Path | str = PROJECT_PATH,
) -> dict[str, Any]:
    """Reload, mutate, and save a project while holding the cross-process project lock."""
    project_path = Path(path)
    with _project_storage_lock(project_path):
        project = _load_project_unlocked(project_path)
        updated = mutator(project)
        if updated is None:
            updated = project
        if not isinstance(updated, dict):
            raise TypeError("project mutator must return a project dict or None")
        return _save_project_unlocked(updated, project_path)


def _load_project_unlocked(project_path: Path) -> dict[str, Any]:
    if project_path == PROJECT_PATH:
        active_id = _read_project_index().get("active_project_id")
        if isinstance(active_id, str) and _project_archive_path(active_id).exists():
            return _load_project_archive(active_id)
        if PROJECT_PATH.exists():
            project_id = _ensure_active_project_archive_id()
            return _load_project_archive(project_id)
        project = _project_model().default_project()
        project_id = _new_project_archive_id()
        _write_project_archive(project_id, project)
        _write_project_index(project_id)
        return project

    return _load_project_file(project_path)


def _save_project_unlocked(project: dict[str, Any], project_path: Path) -> dict[str, Any]:
    if project_path != PROJECT_PATH:
        return _save_project_file(project, project_path)

    project_id = _active_project_archive_id_or_create(project)
    return _write_project_archive(project_id, project)


def save_project_as_archive(
    project: dict[str, Any],
    *,
    title: str = "",
    activate: bool = True,
) -> dict[str, Any]:
    with _project_storage_lock(PROJECT_PATH):
        next_project = deepcopy(project)
        if title:
            next_project["title"] = title
        project_id = _new_project_archive_id()
        saved = _write_project_archive(project_id, next_project)
        if activate:
            _write_project_index(project_id)
        return saved


def set_active_project_archive(project_id: str) -> dict[str, Any]:
    with _project_storage_lock(PROJECT_PATH):
        safe_id = _safe_project_archive_id(project_id)
        archive_path = _project_archive_path(safe_id)
        if not archive_path.exists() or not archive_path.is_file():
            raise ValueError("project archive not found")
        project = _load_project_archive(safe_id)
        _write_project_index(safe_id)
        return project


def active_project_archive_id() -> str:
    with _project_storage_lock(PROJECT_PATH):
        return _ensure_active_project_archive_id()


def list_project_archives(limit: int = 50) -> list[dict[str, Any]]:
    with _project_storage_lock(PROJECT_PATH):
        music_project = _project_model()
        active_id = _ensure_active_project_archive_id()
        archives: list[dict[str, Any]] = []
        for path in PROJECTS_DIR.glob("*.json"):
            try:
                record = _read_project_archive_record(path)
            except (OSError, json.JSONDecodeError, ValueError, KeyError, UnicodeDecodeError):
                continue
            project = music_project.normalize_project(
                cast(dict[str, Any], record.get("project") or {})
            )
            summary = music_project.project_summary(project)
            project_id = str(record.get("id") or path.stem)
            archives.append(
                {
                    "id": project_id,
                    "title": str(record.get("title") or project.get("title") or "ATRI Session"),
                    "saved_at": str(record.get("saved_at") or project.get("updated_at") or ""),
                    "updated_at": str(project.get("updated_at") or ""),
                    "track_count": int(summary.get("track_count", 0)),
                    "note_count": int(summary.get("note_count", 0)),
                    "tempo": float(project.get("tempo", 120.0)),
                    "time_signature": project.get("time_signature", [4, 4]),
                    "active": project_id == active_id,
                }
            )
        archives.sort(key=lambda item: str(item.get("saved_at") or ""), reverse=True)
        return archives[: max(1, int(limit or 50))]


def _load_project_file(project_path: Path) -> dict[str, Any]:
    music_project = _project_model()
    if not project_path.exists():
        project = music_project.default_project()
        _save_project_file(project, project_path)
        return project

    try:
        loaded = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = music_project.default_project()
    return music_project.normalize_project(cast(dict[str, Any], loaded))


def _save_project_file(project: dict[str, Any], path: Path) -> dict[str, Any]:
    normalized = _project_model().normalize_project(project)
    normalized["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(normalized, ensure_ascii=False, indent=2),
        prefix=".music_project_",
    )
    return normalized


def _ensure_active_project_archive_id() -> str:
    active_id = _read_project_index().get("active_project_id")
    if isinstance(active_id, str) and _project_archive_path(active_id).exists():
        return active_id

    if PROJECT_PATH.exists():
        project = _load_project_file(PROJECT_PATH)
    else:
        project = _project_model().default_project()
    project_id = _new_project_archive_id()
    _write_project_archive(project_id, project)
    _write_project_index(project_id)
    return project_id


def _active_project_archive_id_or_create(project: dict[str, Any]) -> str:
    active_id = _read_project_index().get("active_project_id")
    if isinstance(active_id, str) and _project_archive_path(active_id).exists():
        return active_id
    project_id = _new_project_archive_id()
    _write_project_index(project_id)
    return project_id


def _load_project_archive(project_id: str) -> dict[str, Any]:
    record = _read_project_archive_record(_project_archive_path(project_id))
    return _project_model().normalize_project(cast(dict[str, Any], record.get("project") or {}))


def _write_project_archive(project_id: str, project: dict[str, Any]) -> dict[str, Any]:
    safe_id = _safe_project_archive_id(project_id)
    normalized = _project_model().normalize_project(project)
    normalized["updated_at"] = _now_iso()
    record = {
        "id": safe_id,
        "title": normalized.get("title") or "ATRI Session",
        "saved_at": normalized["updated_at"],
        "project": normalized,
    }
    path = _project_archive_path(safe_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(record, ensure_ascii=False, indent=2),
        prefix=".music_project_",
    )
    return normalized


def _read_project_archive_record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project archive must be an object")
    project = data.get("project")
    if isinstance(project, dict):
        return cast(dict[str, Any], data)
    return {
        "id": path.stem,
        "title": str(data.get("title") or path.stem),
        "saved_at": str(data.get("updated_at") or ""),
        "project": data,
    }


def _read_project_index() -> dict[str, Any]:
    try:
        data = json.loads(PROJECT_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _write_project_index(project_id: str) -> None:
    PROJECT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        PROJECT_INDEX_PATH,
        json.dumps(
            {
                "active_project_id": _safe_project_archive_id(project_id),
                "updated_at": _now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        prefix=".music_project_index_",
    )


def _project_archive_path(project_id: str) -> Path:
    return PROJECTS_DIR / f"{_safe_project_archive_id(project_id)}.json"


def _new_project_archive_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"project_{stamp}_{uuid4().hex[:8]}"


def _safe_project_archive_id(project_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project_id or "").strip()).strip("._-")
    if not safe or safe in {".", ".."} or ".." in safe:
        raise ValueError("invalid project archive id")
    return safe
