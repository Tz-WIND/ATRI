from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO_COMPONENT = ROOT / "frontend" / "src" / "components" / "music" / "MusicStudio.vue"
DAW_HOST = ROOT / "frontend" / "src" / "composables" / "useDawHost.js"
API = ROOT / "frontend" / "src" / "composables" / "useApi.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_music_studio_exposes_track_delete_control():
    text = _read(STUDIO_COMPONENT)

    assert "deleteTrack," in text
    assert '@click.stop="deleteTrack(track.id)"' in text
    assert 'title="Delete track"' in text
    assert 'aria-label="Delete track"' in text
    assert 'class="track-delete"' in text
    assert ">Del</button>" not in text


def test_daw_host_and_api_support_deleting_tracks():
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert "async function deleteTrack(trackId)" in host_text
    assert "studioDeleteTrack: (trackId)" in api_text
    assert "method: 'DELETE'" in api_text
