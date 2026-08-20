from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, get_ident

import pytest
from quart import Quart

from dashboard import music as music_routes


@pytest.fixture
def thread_calls(monkeypatch):
    calls: list[str] = []

    async def fake_to_thread(fn, /, *args, **kwargs):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return fn(*args, **kwargs)

    monkeypatch.setattr(music_routes.asyncio, "to_thread", fake_to_thread)
    return calls


def _music_client() -> Quart:
    app = Quart(__name__)
    app.register_blueprint(music_routes.bp)
    return app


@pytest.mark.asyncio
async def test_get_library_reads_cache_off_event_loop(tmp_path, monkeypatch, thread_calls):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "data" / "music_cache.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps([{"id": "song-1", "title": "One", "path": "a.wav"}]),
        encoding="utf-8",
    )

    response = await _music_client().test_client().get("/api/music/library")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["count"] == 1
    assert body["songs"][0]["id"] == "song-1"
    assert "_read_library_cache" in thread_calls


@pytest.mark.asyncio
async def test_scan_library_scans_directories_off_event_loop(tmp_path, monkeypatch, thread_calls):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(music_routes, "_music_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(
        music_routes.music_library,
        "scan_music_directories",
        lambda dirs: [{"id": "scanned", "title": "Scanned", "path": "b.wav"}],
    )

    response = await _music_client().test_client().post("/api/music/scan")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["count"] == 1
    assert (
        json.loads((tmp_path / "data" / "music_cache.json").read_text(encoding="utf-8"))[0]["id"]
        == "scanned"
    )
    assert "_scan_and_cache_library" in thread_calls


def test_scan_and_cache_library_writes_cache_atomically(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    songs = [{"id": "atomic", "title": "Atomic", "path": "atomic.wav"}]
    writes: list[tuple[Path, str, dict[str, object]]] = []
    monkeypatch.setattr(music_routes, "_music_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(
        music_routes.music_library,
        "scan_music_directories",
        lambda dirs: songs,
    )

    def record_atomic_write(path, data, **kwargs):
        writes.append((Path(path), data, kwargs))

    monkeypatch.setattr(music_routes, "atomic_write_text", record_atomic_write)

    assert music_routes._scan_and_cache_library() == songs
    assert len(writes) == 1
    path, payload, options = writes[0]
    assert path == Path("data/music_cache.json")
    assert json.loads(payload) == songs
    assert options == {"encoding": "utf-8"}


def test_scan_and_cache_library_serializes_concurrent_scans(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(music_routes, "_music_dirs", lambda: [str(tmp_path)])
    first_started = Event()
    second_started = Event()
    release_first = Event()
    call_lock = Lock()
    call_count = 0

    def controlled_scan(dirs):
        nonlocal call_count
        with call_lock:
            call_count += 1
            call_number = call_count
        if call_number == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
            return [{"id": "first", "path": "first.wav"}]
        second_started.set()
        return [{"id": "second", "path": "second.wav"}]

    monkeypatch.setattr(
        music_routes.music_library,
        "scan_music_directories",
        controlled_scan,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(music_routes._scan_and_cache_library)
        assert first_started.wait(timeout=1)
        second = executor.submit(music_routes._scan_and_cache_library)
        try:
            assert not second_started.wait(timeout=0.2)
        finally:
            release_first.set()
        assert first.result(timeout=2)[0]["id"] == "first"
        assert second.result(timeout=2)[0]["id"] == "second"

    cache = json.loads((tmp_path / "data" / "music_cache.json").read_text(encoding="utf-8"))
    assert cache[0]["id"] == "second"


@pytest.mark.asyncio
async def test_studio_project_loads_disk_state_off_event_loop(tmp_path, monkeypatch, thread_calls):
    monkeypatch.chdir(tmp_path)
    music_routes.save_project(
        {
            "title": "Threaded",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "tracks": [{"id": 1, "type": "instrument", "name": "Lead", "notes": []}],
        }
    )
    thread_calls.clear()

    response = await _music_client().test_client().get("/api/music/studio/project")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["project"]["title"] == "Threaded"
    assert "_studio_project_snapshot" in thread_calls


def test_read_library_cache_uses_cache_path_helper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "custom" / "library.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{"id": "custom-cache"}]), encoding="utf-8")
    monkeypatch.setattr(music_routes, "_cache_path", lambda: cache)

    assert music_routes._read_library_cache() == [{"id": "custom-cache"}]


@pytest.mark.parametrize(
    ("helper_name", "reader_name"),
    [
        ("_cover_for_song", "_get_cover_bytes"),
        ("_lyrics_for_song", "_find_lyrics"),
    ],
)
@pytest.mark.parametrize("path_state", ["outside", "missing_file"])
def test_cover_and_lyrics_only_read_streamable_library_paths(
    tmp_path,
    monkeypatch,
    helper_name,
    reader_name,
    path_state,
):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    if path_state == "outside":
        song_path = tmp_path / "private.mp3"
        song_path.write_bytes(b"private")
    else:
        song_path = music_dir / "missing.mp3"

    monkeypatch.setattr(music_routes, "_music_dirs", lambda: [str(music_dir)])
    monkeypatch.setattr(
        music_routes,
        "_read_library_cache",
        lambda: [{"id": "unsafe", "path": str(song_path)}],
    )
    reads = []
    monkeypatch.setattr(music_routes, reader_name, lambda path: reads.append(path))

    status, result = getattr(music_routes, helper_name)("unsafe")

    assert status == path_state
    assert result is None
    assert reads == []


def test_project_payload_uses_supplied_active_project_id_without_storage_access(monkeypatch):
    project = music_routes.default_project()

    def unexpected_storage_read():
        raise AssertionError("project payload must not read project storage")

    monkeypatch.setattr(music_routes, "active_project_archive_id", unexpected_storage_read)

    payload = music_routes._project_payload(project, active_project_id="project-active")

    assert payload["active_project_id"] == "project-active"
    assert payload["project"] is project
    assert len(payload["revision"]) == 64


@pytest.mark.asyncio
async def test_save_project_copy_builds_payload_and_listing_in_one_worker(monkeypatch):
    project = music_routes.default_project()
    event_loop_thread_id = get_ident()
    active_id_threads = []

    monkeypatch.setattr(music_routes, "load_project", lambda: project)
    monkeypatch.setattr(
        music_routes,
        "save_project_as_archive",
        lambda project, **kwargs: project,
    )
    monkeypatch.setattr(music_routes, "_host_snapshot", lambda: {})

    def project_archives_snapshot():
        active_id_threads.append(get_ident())
        return [], "project-active"

    async def ignore_broadcast(project):
        return None

    monkeypatch.setattr(
        music_routes,
        "project_archives_snapshot",
        project_archives_snapshot,
    )
    monkeypatch.setattr(music_routes, "_broadcast_project", ignore_broadcast)

    response = (
        await _music_client()
        .test_client()
        .post(
            "/api/music/studio/projects/save-copy",
            json={"sync": False},
        )
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["active_project_id"] == "project-active"
    assert len(active_id_threads) == 1
    assert active_id_threads[0] != event_loop_thread_id
