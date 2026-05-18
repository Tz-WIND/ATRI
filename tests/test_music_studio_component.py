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


def test_music_studio_supports_track_type_and_audio_channel_controls():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert '@click="openTrackCreateDialog"' in studio_text
    assert 'v-if="trackCreateDialogOpen"' in studio_text
    assert 'class="track-create-dialog"' in studio_text
    assert 'role="dialog"' in studio_text
    assert 'aria-modal="true"' in studio_text
    assert 'v-model="trackCreateName"' in studio_text
    assert 'v-model="trackCreateColor"' in studio_text
    assert 'type="color"' in studio_text
    assert "trackCreatePalette" in studio_text
    assert '@click="trackCreateColor = color"' in studio_text
    assert 'v-model="trackCreateType"' in studio_text
    assert '<option value="instrument">' in studio_text
    assert '<option value="audio">' in studio_text
    assert "v-if=\"trackCreateType === 'audio'\"" in studio_text
    assert '@click="createSelectedTrack"' in studio_text
    assert '@click="closeTrackCreateDialog"' in studio_text
    assert "function openTrackCreateDialog()" in studio_text
    assert "function closeTrackCreateDialog()" in studio_text
    assert "function createSelectedTrack()" in studio_text
    assert "color: trackCreateColor.value" in studio_text
    assert "isInstrumentTrack(track)" in studio_text
    assert "isAudioTrack(track)" in studio_text
    assert (
        '@change.stop="updateTrack(track.id, { channel_type: $event.target.value })"' in studio_text
    )
    assert "async function createTrack(name = 'Instrument', options = {})" in host_text
    assert "studioCreateTrack: (name, options = {})" in api_text
    assert "body: JSON.stringify({ name, ...options })" in api_text


def test_music_studio_supports_external_audio_drop_import():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert '@drop.prevent="onAudioDrop"' in studio_text
    assert "prepareAudioImport(file)" in studio_text
    assert "file," in studio_text
    assert "encodeAudioBufferToWav" not in studio_text
    assert "drawClipAudioPreview(ctx, clip, rect, track)" in studio_text
    assert "async function importAudioFile(file, metadata = {})" in host_text
    assert "studioAudioImport: (file, metadata = {})" in api_text
    assert "/api/music/studio/audio/import" in api_text


def test_music_studio_audio_drop_matches_host_supported_import_formats():
    studio_text = _read(STUDIO_COMPONENT)

    assert (
        "const supportedAudioImportExtensions = ['aac', 'flac', 'm4a', 'mp3', 'wav']" in studio_text
    )
    assert "supportedAudioImportExtensions.some" in studio_text
    assert "ogg|opus" not in studio_text
    assert "wma" not in studio_text
    assert "aiff" not in studio_text


def test_music_studio_keeps_arrangement_track_list_fixed_while_scrolling():
    studio_text = _read(STUDIO_COMPONENT)

    assert '@scroll="syncArrangementScroll"' in studio_text
    assert "'--arrangement-scroll-left': `${arrangementScrollLeft.value}px`" in studio_text
    assert "function syncArrangementScroll(event)" in studio_text
    assert "translateX(var(--arrangement-scroll-left, 0px))" in studio_text


def test_music_studio_arrangement_body_wheel_uses_shift_for_horizontal_scroll():
    studio_text = _read(STUDIO_COMPONENT)

    assert '@wheel="onArrangementWheel"' in studio_text
    assert "function scrollArrangementHorizontallyFromWheel(event, wrap)" in studio_text
    assert "if (event.shiftKey && !event.ctrlKey && !event.metaKey)" in studio_text
    assert "if (!event.ctrlKey && !event.metaKey) return" in studio_text
    assert "const wheelDelta = event.deltaX || event.deltaY" in studio_text
    assert "wrap.scrollLeft = clamp(wrap.scrollLeft + wheelDelta" in studio_text


def test_music_studio_audio_waveform_uses_track_color_background():
    studio_text = _read(STUDIO_COMPONENT)

    assert (
        "ctx.fillStyle = hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.72)"
        in studio_text
    )
    assert "function drawClipAudioPreview(ctx, clip, rect, track)" in studio_text
    assert "const trackColor = clip.color || track.color" in studio_text
    assert "'rgba(88, 167, 184, 0.68)'" not in studio_text


def test_music_studio_audio_waveform_uses_zrythm_region_style():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function waveformPointMetrics(point)" in studio_text
    assert (
        "function drawZrythmAudioRegionFrame(ctx, clip, rect, track, selected, active)"
        in studio_text
    )
    assert "function drawZrythmWaveformEnvelope(ctx, points, bounds)" in studio_text
    assert "function zrythmRegionContentColor()" in studio_text
    assert "function zrythmRegionOutlineColor()" in studio_text
    assert "drawWaveformTransientTexture" not in studio_text
    assert "ctx.shadowBlur = 7" not in studio_text
    assert "rms:" in studio_text
    assert "min:" in studio_text
    assert "max:" in studio_text
