from pathlib import Path

COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "music"
    / "MusicFullPlayer.vue"
)


def _component_text() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_music_full_player_keeps_apple_music_layout_structure():
    text = _component_text()

    assert 'class="fp-bg-img fp-bg-img-soft"' in text
    assert 'class="fp-bg-img fp-bg-img-main"' in text
    assert 'class="fp-bg-frost"' in text
    assert 'class="fp-artwork-wrap"' in text
    assert 'class="fp-right"' in text
    assert 'class="lyrics-container"' in text
    assert "grid-template-columns: minmax(300px, 400px) minmax(360px, 600px);" in text
    assert "mask-image: linear-gradient" in text


def test_music_full_player_preserves_playback_and_playlist_bindings():
    text = _component_text()

    assert '@click="togglePlay"' in text
    assert '@click="prev"' in text
    assert '@click="next"' in text
    assert '@click="seekToLyric(line.time)"' in text
    assert "showPlaylist = ref(false)" in text
    assert '@click.stop="showPlaylist = !showPlaylist"' in text
    assert '@click="playSongAt(i)"' in text
    assert "watch(currentIndex" in text
