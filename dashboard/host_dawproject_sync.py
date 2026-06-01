"""DAWproject snapshot inbox sync for the DAW agent Host Project workspace."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from core import logger
from core.music_export import read_dawproject_archive
from core.music_project import (
    active_project_archive_id,
    save_project,
    save_project_as_archive,
    set_active_project_archive,
)
from core.utils import atomic_write_text

HOST_DAWPROJECT_SYNC_INBOX_DIR = Path("data/music_workstation/host_sync_inbox")
HOST_DAWPROJECT_SYNC_STATE_PATH = Path("data/music_workstation/host_sync_state.json")
HOST_DAWPROJECT_SYNC_REQUESTS_DIR = Path("data/music_workstation/host_sync_requests")
HOST_DAWPROJECT_SYNC_REQUEST_LATEST_PATH = HOST_DAWPROJECT_SYNC_REQUESTS_DIR / "latest.json"
HOST_DAWPROJECT_SNAPSHOT_INDEX_PATH = Path("data/music_workstation/host_snapshot_index.json")


def _created_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def dawproject_snapshot_status() -> dict[str, Any]:
    """Return the latest DAWproject snapshot and export-request state for local UI."""
    HOST_DAWPROJECT_SYNC_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    latest = _latest_host_dawproject_sync_file()
    request = _read_host_dawproject_export_request()
    return {
        "ok": True,
        "format": "dawproject",
        "inbox_path": str(HOST_DAWPROJECT_SYNC_INBOX_DIR.resolve()),
        "latest_snapshot": _host_dawproject_snapshot_entry(latest) if latest else None,
        "export_request": request,
    }


def request_host_dawproject_snapshot_export(
    *,
    host: str = "studio_one",
    source: str = "manual",
    instance_id: str = "",
) -> dict[str, Any]:
    """Write a file-based export request consumed by optional host helper scripts."""
    safe_host = _host_dawproject_export_host_id(host)
    HOST_DAWPROJECT_SYNC_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    HOST_DAWPROJECT_SYNC_REQUESTS_DIR.mkdir(parents=True, exist_ok=True)
    request_id = uuid4().hex[:12]
    output_path = HOST_DAWPROJECT_SYNC_INBOX_DIR / _host_dawproject_snapshot_filename(safe_host)
    request = {
        "schema_version": 1,
        "id": request_id,
        "action": "export_dawproject_snapshot",
        "host": safe_host,
        "source": str(source or "manual").strip()[:64] or "manual",
        "instance_id": str(instance_id or "").strip()[:128],
        "requested_at": _created_at(),
        "inbox_path": str(HOST_DAWPROJECT_SYNC_INBOX_DIR.resolve()),
        "output_path": str(output_path.resolve()),
        "request_path": str(HOST_DAWPROJECT_SYNC_REQUEST_LATEST_PATH.resolve()),
    }
    payload = json.dumps(request, ensure_ascii=False, indent=2)
    atomic_write_text(
        HOST_DAWPROJECT_SYNC_REQUESTS_DIR / f"{request_id}.json",
        payload,
        prefix=".host_sync_request_",
    )
    atomic_write_text(
        HOST_DAWPROJECT_SYNC_REQUEST_LATEST_PATH,
        payload,
        prefix=".host_sync_request_",
    )
    return request


async def sync_latest_host_dawproject_for_daw_agent() -> dict[str, Any]:
    """Import the newest DAWproject snapshot before DAW agent chat."""
    latest = _latest_host_dawproject_sync_file()
    if latest is None:
        return {"status": "missing", "format": "dawproject"}

    try:
        file_state = _host_dawproject_sync_file_state(latest)
    except OSError as e:
        return {
            "status": "error",
            "format": "dawproject",
            "filename": latest.name,
            "error": str(e),
        }

    previous_state = _read_host_dawproject_sync_state()
    previous_summary = previous_state.get("summary") if isinstance(previous_state, dict) else None
    if _host_dawproject_sync_state_matches(previous_state, file_state) and isinstance(
        previous_summary,
        dict,
    ):
        return {**_host_project_sync_prompt_context(previous_summary), "status": "unchanged"}

    snapshot_entry = _host_dawproject_snapshot_entry(latest)
    if not snapshot_entry.get("ready"):
        return {
            "status": "pending",
            "format": "dawproject",
            "filename": latest.name,
            "reason": snapshot_entry.get("reason", "snapshot is not ready"),
        }

    try:
        project, import_summary = read_dawproject_archive(latest)
        project, archive_state = _save_dawproject_snapshot_project(project, latest)
        from dashboard import music as music_routes

        sync = await music_routes._sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        logger.warning("Failed to import DAWproject snapshot %s: %s", latest, e)
        return {
            "status": "error",
            "format": "dawproject",
            "filename": latest.name,
            "error": str(e),
        }

    summary = {
        "status": "imported",
        **_host_project_sync_prompt_context(
            {
                **import_summary,
                "filename": latest.name,
            }
        ),
        "source": str(latest),
        "snapshot_key": archive_state["snapshot_key"],
        "archive_mode": archive_state["mode"],
        "active_project_id": active_project_archive_id(),
    }
    _write_host_dawproject_sync_state(file_state, summary)
    return summary


def host_project_sync_prompt_context(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Return the bounded DAWproject snapshot summary safe for DAW agent context."""
    return _host_project_sync_prompt_context(summary or {})


def _latest_host_dawproject_sync_file() -> Path | None:
    HOST_DAWPROJECT_SYNC_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[int, str, Path]] = []
    for path in HOST_DAWPROJECT_SYNC_INBOX_DIR.glob("*.dawproject"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime_ns, path.name.lower(), path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _host_dawproject_sync_file_state(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "mtime_ns": int(stat.st_mtime_ns),
        "size": int(stat.st_size),
    }


def _read_host_dawproject_sync_state() -> dict[str, Any]:
    try:
        data = json.loads(HOST_DAWPROJECT_SYNC_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_host_dawproject_export_request() -> dict[str, Any] | None:
    try:
        data = json.loads(HOST_DAWPROJECT_SYNC_REQUEST_LATEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _public_host_dawproject_export_request(data)


def _write_host_dawproject_sync_state(
    file_state: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    HOST_DAWPROJECT_SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        **file_state,
        "imported_at": _created_at(),
        "summary": _host_project_sync_prompt_context(summary),
        "active_project_id": str(summary.get("active_project_id") or ""),
    }
    atomic_write_text(
        HOST_DAWPROJECT_SYNC_STATE_PATH,
        json.dumps(state, ensure_ascii=False, indent=2),
        prefix=".host_sync_state_",
    )


def _save_dawproject_snapshot_project(
    project: dict[str, Any],
    snapshot_path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    snapshot_key = _host_dawproject_snapshot_key(snapshot_path)
    index = _read_host_dawproject_snapshot_index()
    archives_by_key = index.setdefault("archives_by_key", {})
    project_id = str(archives_by_key.get(snapshot_key) or "")
    mode = "created"
    if project_id:
        try:
            set_active_project_archive(project_id)
            saved = save_project(project)
            mode = "updated"
        except ValueError:
            project_id = ""
    if not project_id:
        saved = save_project_as_archive(project, activate=True)
        project_id = active_project_archive_id()
        archives_by_key[snapshot_key] = project_id
    index["updated_at"] = _created_at()
    _write_host_dawproject_snapshot_index(index)
    return saved, {"snapshot_key": snapshot_key, "project_id": project_id, "mode": mode}


def _read_host_dawproject_snapshot_index() -> dict[str, Any]:
    try:
        data = json.loads(HOST_DAWPROJECT_SNAPSHOT_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"archives_by_key": {}}
    if not isinstance(data, dict):
        return {"archives_by_key": {}}
    archives = data.get("archives_by_key")
    if not isinstance(archives, dict):
        data["archives_by_key"] = {}
    return data


def _write_host_dawproject_snapshot_index(index: dict[str, Any]) -> None:
    HOST_DAWPROJECT_SNAPSHOT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        HOST_DAWPROJECT_SNAPSHOT_INDEX_PATH,
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
        prefix=".host_snapshot_index_",
    )


def _host_dawproject_sync_state_matches(
    state: dict[str, Any],
    file_state: dict[str, Any],
) -> bool:
    return (
        str(state.get("path") or "") == str(file_state.get("path") or "")
        and int(state.get("mtime_ns") or -1) == int(file_state.get("mtime_ns") or -2)
        and int(state.get("size") or -1) == int(file_state.get("size") or -2)
    )


def _host_project_sync_prompt_context(summary: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    status = str(summary.get("status") or "").strip()
    if status in {"missing", "pending", "unchanged", "imported", "error"}:
        context["status"] = status
    format_name = str(summary.get("format") or "").strip().lower()
    if format_name == "dawproject":
        context["format"] = format_name
    filename = str(summary.get("filename") or "").strip()
    if filename:
        context["filename"] = filename[:128]
    for key in ("track_count", "midi_clip_count", "note_count"):
        try:
            value = int(summary.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        context[key] = max(0, min(value, 1_000_000))
    return context


def _host_dawproject_snapshot_key(path: Path) -> str:
    stem = path.stem.strip()
    normalized = stem
    normalized = re.sub(r"\s*[\(\uff08]\d+[\)\uff09]\s*$", "", normalized).strip()
    timestamp_patterns = [
        r"[\s._-]+20\d{2}[-_. ]?\d{2}[-_. ]?\d{2}[\s._-]+\d{1,2}[-_.: ]?\d{2}[-_.: ]?\d{2}$",
        r"[\s._-]+20\d{6}[\s._-]?\d{6}$",
        r"[\s._-]+20\d{2}[-_.]\d{2}[-_.]\d{2}[\s._-]+\d{1,2}[-_.]\d{2}$",
    ]
    for pattern in timestamp_patterns:
        normalized = re.sub(pattern, "", normalized).strip()
    normalized = re.sub(r"[\s._-]+", " ", normalized).strip().lower()
    if not normalized:
        normalized = re.sub(r"[\s._-]+", " ", stem).strip().lower()
    return normalized or "dawproject snapshot"


def _host_dawproject_snapshot_entry(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as e:
        return {
            "format": "dawproject",
            "filename": path.name,
            "path": str(path.resolve()),
            "ready": False,
            "reason": str(e),
        }
    ready, reason = _host_dawproject_snapshot_ready(path)
    return {
        "format": "dawproject",
        "filename": path.name,
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "ready": ready,
        "reason": reason,
    }


def _host_dawproject_snapshot_ready(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.replace("\\", "/").lower() for name in archive.namelist()}
    except zipfile.BadZipFile:
        return False, "snapshot is still being written or is not a valid DAWproject archive"
    except OSError as e:
        return False, str(e)
    if "project.xml" not in names and not any(name.endswith("/project.xml") for name in names):
        return False, "snapshot archive is missing project.xml"
    return True, ""


def _host_dawproject_export_host_id(host: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(host or "").strip().lower()).strip("_")
    aliases = {
        "studio_one": "studio_one",
        "studioone": "studio_one",
        "presonus_studio_one": "studio_one",
        "bitwig": "bitwig",
        "bitwig_studio": "bitwig",
        "cubase": "cubase",
        "nuendo": "cubase",
    }
    return aliases.get(normalized, normalized or "studio_one")


def _host_dawproject_snapshot_filename(host: str) -> str:
    filenames = {
        "studio_one": "studio-one-latest.dawproject",
        "bitwig": "bitwig-latest.dawproject",
        "cubase": "cubase-latest.dawproject",
    }
    return filenames.get(host, f"{host.replace('_', '-')}-latest.dawproject")


def _public_host_dawproject_export_request(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(request.get("id") or ""),
        "action": str(request.get("action") or "export_dawproject_snapshot"),
        "host": str(request.get("host") or ""),
        "source": str(request.get("source") or ""),
        "instance_id": str(request.get("instance_id") or ""),
        "requested_at": str(request.get("requested_at") or ""),
        "inbox_path": str(request.get("inbox_path") or ""),
        "output_path": str(request.get("output_path") or ""),
        "request_path": str(request.get("request_path") or ""),
    }
