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


def test_music_studio_exposes_free_time_signature_controls():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="time-signature-picker mono"' in studio_text
    assert 'class="time-signature-display"' in studio_text
    assert 'v-if="timeSignaturePopoverOpen"' in studio_text
    assert 'class="time-signature-popover"' in studio_text
    assert 'v-model.number="timeSignatureNumerator"' in studio_text
    assert (
        '@click.stop="timeSignatureDenominatorPopoverOpen ='
        ' !timeSignatureDenominatorPopoverOpen"'
    ) in studio_text
    assert 'v-if="timeSignatureDenominatorPopoverOpen"' in studio_text
    assert "const timeSignatureDenominatorOptions = [2, 4, 8, 16, 32]" in studio_text
    assert "@change=\"updateTimeSignature\"" in studio_text
    assert "timeSignatureLabel" in studio_text
    assert "timeSignatureDenominatorLabel" in studio_text
    assert "async function updateTimeSignature()" in studio_text
    assert "async function setTimeSignatureDenominator(denominator)" in studio_text
    assert "nextProject.time_signature = [numerator, denominator]" in studio_text


def test_music_studio_exposes_editable_tempo_control():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'v-model.number="tempoInput"' in studio_text
    assert 'aria-label="Tempo BPM"' in studio_text
    assert '@change="updateTempo"' in studio_text
    assert '@keydown.enter="updateTempo"' in studio_text
    assert '@wheel.prevent="onTempoWheel"' in studio_text
    assert "function normalizeTempo(value)" in studio_text
    assert "async function updateTempo()" in studio_text
    assert "function onTempoWheel(event)" in studio_text
    assert "function scheduleTempoUpdate()" in studio_text
    assert "nextProject.tempo = nextTempo" in studio_text


def test_music_studio_meter_beats_respects_time_signature_denominator():
    """meterBeats must use both numerator and denominator, computing bar length
    in quarter-note beats.  e.g. 6/8 => 6 * (4/8) = 3 beats/bar;
    2/2 => 2 * (4/2) = 4 beats/bar; 4/4 => 4 beats/bar (unchanged)."""
    studio_text = _read(STUDIO_COMPONENT)

    # meterBeats accesses both parts of time_signature
    assert "project.value?.time_signature?.[0]" in studio_text
    assert "project.value?.time_signature?.[1]" in studio_text
    # denominator-aware bar-length formula
    assert "4 / denominator" in studio_text
    # normalizeTimeSignatureDenominator is called inside the meterBeats computed
    assert (
        "normalizeTimeSignatureDenominator(project.value?.time_signature?.[1])"
        in studio_text
    )


def test_music_studio_beat_unit_respects_time_signature_for_beat_numbering():
    """beatUnit = meterBeats / numerator controls beat-within-bar numbering.
    positionLabel and pianoRulerBeatLabel must use beatUnit so that 6/8 shows
    beat 2 at position 0.5, not beat 1."""
    studio_text = _read(STUDIO_COMPONENT)

    # beatUnit computed exists
    assert "const beatUnit = computed(() => {" in studio_text
    assert "meterBeats.value / numerator" in studio_text
    # positionLabel uses beatUnit for beat-in-bar and ticks
    assert "const unit = beatUnit.value" in studio_text
    assert "posInBar % unit" in studio_text
    # pianoRulerBeatLabel uses beatUnit
    assert "const barLen = meterBeats.value" in studio_text
    assert "const unit = beatUnit.value" in studio_text


def test_music_studio_grid_overlays_bar_lines_at_fractional_positions():
    """Grids must overlay bar lines at n * meterBeats positions so that
    fractional bar lengths (3/8 => 1.5, 5/8 => 2.5) don't miss bar boundaries
    that fall between integer quarter-note beats."""
    studio_text = _read(STUDIO_COMPONENT)

    # paintGrid (arrangement) overlays bar lines
    assert "function paintGrid(ctx, width, height, offsetX, offsetY)" in studio_text
    assert "const barLen = meterBeats.value" in studio_text
    # Bar line overlay loop at fractional positions
    assert "bar * barLen <= beats" in studio_text
    # paintControllerGrid does the same
    assert "barLen * pianoPxPerBeat.value" in studio_text
    # drawPianoRuler bar line overlay
    assert (
        "// Bar lines overlaid at bar boundaries"
        " (handles fractional barLen like 3/8=1.5)"
    ) in studio_text


def test_music_studio_piano_ruler_draws_decimal_beat_labels():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function pianoRulerBeatLabel(absoluteBeat)" in studio_text
    assert "return beatInBar === 1 ? String(bar) : `${bar}.${beatInBar}`" in studio_text
    assert "ctx.fillText(pianoRulerBeatLabel(absoluteBeat), x + 5, 16)" in studio_text


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
