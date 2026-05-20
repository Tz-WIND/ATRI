from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO_COMPONENT = ROOT / "frontend" / "src" / "components" / "music" / "MusicStudio.vue"
DAW_HOST = ROOT / "frontend" / "src" / "composables" / "useDawHost.js"
API = ROOT / "frontend" / "src" / "composables" / "useApi.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_music_studio_exposes_track_context_delete_control():
    text = _read(STUDIO_COMPONENT)

    assert "deleteTrack," in text
    assert '@contextmenu.prevent="openTrackContextMenu($event, track)"' in text
    assert 'v-if="trackContextMenu.open"' in text
    assert 'class="track-context-menu"' in text
    assert '@click="deleteTrackFromContextMenu"' in text
    assert "function openTrackContextMenu(event, track)" in text
    assert "function deleteTrackFromContextMenu()" in text
    assert 'class="track-delete"' not in text
    assert 'aria-label="Delete track"' not in text
    assert ">Del</button>" not in text


def test_music_studio_track_row_truncates_long_labels_and_keeps_flags_right():
    text = _read(STUDIO_COMPONENT)

    assert 'class="track-title-text"' in text
    assert 'class="track-meta-text"' in text
    assert ".track-title-text," in text
    assert ".track-meta-text {" in text
    assert "text-overflow: ellipsis;" in text
    assert "grid-template-columns: 4px minmax(0, 1fr) auto;" in text
    assert "justify-self: end;" in text
    assert "margin-left: auto;" in text


def test_music_studio_track_rows_match_arrangement_track_height():
    text = _read(STUDIO_COMPONENT)

    assert "const arrangementTrackH = 72" in text
    assert ".track-row {\n  width: 100%;\n  height: 72px;" in text
    assert "  min-height: 72px;\n" not in text
    assert "  overflow: hidden;" in text
    assert ".track-title-text {\n  color: var(--t1);\n  line-height: 16px;\n}" in text
    assert (
        ".track-meta-text {\n  color: var(--t4);\n  font-size: 11px;\n  line-height: 13px;\n}"
        in text
    )
    assert ".track-plugin-bar {\n  width: 100%;\n  min-width: 0;\n  height: 24px;" in text
    assert "height: 24px;\n  border: 1px solid rgba(229, 236, 245, 0.12);" in text


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


def test_music_studio_exposes_bus_track_creation_and_output_selector():
    studio_text = _read(STUDIO_COMPONENT)

    assert '<option value="bus">' in studio_text
    assert "trackCreateType.value === 'bus'" in studio_text
    assert "output_bus_id" in studio_text
    assert "availableOutputBuses" in studio_text


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
        '@click.stop="timeSignatureDenominatorPopoverOpen = !timeSignatureDenominatorPopoverOpen"'
    ) in studio_text
    assert 'v-if="timeSignatureDenominatorPopoverOpen"' in studio_text
    assert "const timeSignatureDenominatorOptions = [2, 4, 8, 16, 32]" in studio_text
    assert '@change="updateTimeSignature"' in studio_text
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
    assert "normalizeTimeSignatureDenominator(project.value?.time_signature?.[1])" in studio_text


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
        "// Bar lines overlaid at bar boundaries (handles fractional barLen like 3/8=1.5)"
    ) in studio_text


def test_music_studio_piano_and_arrangement_rulers_share_decimal_beat_labels():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function rulerBeatLabel(absoluteBeat)" in studio_text
    assert "function drawBeatRulerLabels(ctx, {" in studio_text
    assert "return beatInBar === 1 ? String(bar) : `${bar}.${beatInBar}`" in studio_text
    assert "drawBeatRulerLabels(ctx, {\n    startBeat: 0," in studio_text
    assert "originX: 0," in studio_text
    assert "drawBeatRulerLabels(ctx, {\n    startBeat: clipStart," in studio_text
    assert "originX: pianoKeyW," in studio_text
    assert (
        "const shouldDrawBeatLabel = metrics.shouldLabel && "
        "(metrics.isBar || scale >= rulerBeatLabelMinScale)"
    ) in studio_text
    assert "ctx.fillText(rulerBeatLabel(absoluteBeat), labelX, Math.min(labelY" in studio_text


def test_music_studio_rulers_draw_scaled_tick_marks_from_quantize_step():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const rulerMajorTickRatio = 1 / 3" in studio_text
    assert "const rulerMinorTickRatio = rulerMajorTickRatio / 2" in studio_text
    assert "const rulerFineTickRatio = rulerMinorTickRatio / 2" in studio_text
    assert "const rulerLabelGap = 2" in studio_text
    assert "function rulerTickStep()" in studio_text
    assert "return activePianoSnapStep.value || snapStep" in studio_text
    assert "function rulerTickMetrics(absoluteBeat)" in studio_text
    assert "heightRatio: rulerMajorTickRatio" in studio_text
    assert "heightRatio: rulerMinorTickRatio" in studio_text
    assert "heightRatio: rulerFineTickRatio" in studio_text
    assert "ctx.font = metrics.isBar ? rulerBarLabelFont : rulerBeatLabelFont" in studio_text
    assert "ctx.lineWidth = metrics.lineWidth" in studio_text
    assert "ctx.moveTo(x, tickBottom - tickHeight)" in studio_text
    assert "const labelX = Math.max(originX + rulerLabelGap, x + rulerLabelGap)" in studio_text
    assert (
        "for (\n    let absoluteBeat = firstMultipleAtOrAfter(startBeat, tickStep);" in studio_text
    )


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


def test_music_studio_track_list_sidebar_can_be_resized():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="track-list-resize-handle"' in studio_text
    assert 'aria-label="Resize track list"' in studio_text
    assert '@pointerdown="startTrackListResize"' in studio_text
    assert "const defaultTrackListWidth = 246" in studio_text
    assert "const minTrackListWidth = 190" in studio_text
    assert "const maxTrackListWidth = 420" in studio_text
    assert "const trackListWidth = ref(defaultTrackListWidth)" in studio_text
    assert "'--track-list-width': `${trackListWidth.value}px`" in studio_text
    assert "function clampTrackListWidth(width)" in studio_text
    assert "function startTrackListResize(event)" in studio_text
    assert "function onTrackListResizeMove(event)" in studio_text
    assert "window.addEventListener('pointermove', onTrackListResizeMove)" in studio_text
    assert "unbindTrackListResize()" in studio_text
    assert "arrangementWrap.value.clientWidth - currentTrackListWidth()" in studio_text
    assert "grid-template-columns: var(--track-list-width) minmax(0, 1fr);" in studio_text
    assert "grid-template-columns: var(--track-list-width) max-content;" in studio_text


def test_music_studio_track_sidebar_drag_reorder_persists_tracks_and_syncs_mixer():
    studio_text = _read(STUDIO_COMPONENT)

    assert ':draggable="canDragTrackRow(track)"' in studio_text
    assert '@dragstart.stop="startTrackReorderDrag($event, track)"' in studio_text
    assert '@dragover.prevent.stop="onTrackReorderDragOver($event, track)"' in studio_text
    assert '@drop.prevent.stop="dropTrackReorder($event, track)"' in studio_text
    assert '@dragend.stop="endTrackReorderDrag"' in studio_text
    assert (
        "const trackReorderDrag = ref({ trackId: null, overTrackId: null, placement: 'after' })"
        in studio_text
    )
    assert (
        "function moveTrackInList(trackList, sourceTrackId, targetTrackId, placement)"
        in studio_text
    )
    assert "async function dropTrackReorder(event, targetTrack)" in studio_text
    assert (
        "nextProject.tracks = moveTrackInList("
        "nextProject.tracks || [], sourceTrackId, targetTrack.id, placement)" in studio_text
    )
    assert ".track-row.reorder-before::before" in studio_text
    assert ".track-row.reorder-after::after" in studio_text
    assert ':key="`mixer-${track.id}`"' in studio_text


def test_music_studio_track_list_sidebar_uses_single_aligned_divider():
    studio_text = _read(STUDIO_COMPONENT)

    assert ".track-list-resize-handle::after" in studio_text
    assert "left: 4px;" in studio_text
    assert "width: 1px;" in studio_text
    assert ".track-list-head {\n  gap: 6px;\n}" in studio_text
    assert (
        ".track-list {\n"
        "  position: relative;\n"
        "  z-index: 4;\n"
        "  grid-column: 1;\n"
        "  grid-row: 1;\n"
        "  align-self: start;\n"
        "  width: var(--track-list-width);\n"
        "  min-width: var(--track-list-width);\n"
        "  background: #202428;\n"
    ) in studio_text
    assert "border-right: 1px solid rgba(229, 236, 245, 0.12);\n  box-shadow" not in studio_text


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


def test_music_studio_exposes_automation_tracks_and_context_creation():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert "isAutomationTrack(track)" in studio_text
    assert "trackTypeLabel(track)" in studio_text
    assert "drawAutomationTrack(ctx, track, index)" in studio_text
    assert "openAutomationMenu($event" in studio_text
    assert "confirmCreateAutomationFromMenu" in studio_text
    assert "createAutomationTrackForTarget" in studio_text
    assert "automationTargetForTrackVolume(track)" in studio_text
    assert "automationTargetForTrackPan(track)" in studio_text
    assert "automation-context-menu" in studio_text
    assert "async function createAutomationTrack(target, options = {})" in host_text
    assert "studioAutomationWrite: (payload)" in api_text
    assert "/api/music/studio/automation" in api_text


def test_music_studio_exposes_plugin_parameter_browser_and_live_set():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert "loadPluginParameters(track.id, slot.id)" in studio_text
    assert "pluginParameterRows(track.id, slot.id)" in studio_text
    assert "automationTargetForPluginParameter(track, slot.id, param)" in studio_text
    assert (
        "setLivePluginParameter(track.id, slot.id, param.index, Number($event.target.value))"
        in studio_text
    )
    assert "async function loadPluginParameters(trackId, slotId = 'instrument')" in host_text
    assert "async function setPluginParameter(trackId, slotId, paramIndex, value)" in host_text
    assert "studioPluginParameters: (trackId, slotId = 'instrument')" in api_text
    assert "studioSetPluginParameter: (payload)" in api_text


def test_music_studio_plugin_names_truncate_like_track_titles():
    studio_text = _read(STUDIO_COMPONENT)

    assert (
        ".track-plugin-select {\n"
        "  min-width: 0;\n"
        "  width: 100%;\n"
        "  height: 24px;\n"
        "  border: 1px solid rgba(229, 236, 245, 0.12);\n"
        "  overflow: hidden;\n"
        "  text-overflow: ellipsis;\n"
        "  white-space: nowrap;\n"
    ) in studio_text
    assert (
        ".mixer-insert-slot select,\n"
        ".mixer-send-row select,\n"
        ".mixer-send-add {\n"
        "  min-width: 0;\n"
        "  width: 100%;\n"
        "  height: 24px;\n"
        "  overflow: hidden;\n"
    ) in studio_text
    assert ".mixer-insert-slot span {" in studio_text
    assert "text-overflow: ellipsis;" in studio_text


def test_music_studio_has_mutually_exclusive_piano_and_mixer_lower_windows():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const lowerEditorMode = ref(null)" in studio_text
    assert "const pianoVisible = computed(() => lowerEditorMode.value === 'piano')" in studio_text
    assert "const mixerVisible = computed(() => lowerEditorMode.value === 'mixer')" in studio_text
    assert '@click="openMixer"' in studio_text
    assert "function openMixer()" in studio_text
    assert "function closeMixer()" in studio_text
    assert "lowerEditorMode.value = 'mixer'" in studio_text
    assert "lowerEditorMode.value = 'piano'" in studio_text
    assert 'v-if="mixerVisible"' in studio_text
    assert 'v-if="pianoVisible && activeMidiClip"' in studio_text


def test_music_studio_mixer_window_replaces_inspector_rack():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="mixer-panel"' in studio_text
    assert ":class=\"['mixer-strip', { active: activeTrack?.id === track.id }]\"" in studio_text
    assert 'v-for="track in mixerTracks"' in studio_text
    assert (
        "const mixerTracks = computed(() => tracks.value.filter("
        "track => !isAutomationTrack(track)))"
        in studio_text
    )
    assert 'class="mixer-strip master-strip"' in studio_text
    assert "Master Bus" in studio_text
    assert 'class="mixer-master-dock"' in studio_text
    assert 'class="plugin-rack"' not in studio_text
    assert "const rackSlots =" not in studio_text
    assert ">Mixer\n          </div>" not in studio_text


def test_music_studio_mixer_uses_dynamic_inserts_duplicate_labels_and_sends():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function mixerInsertSlots(track)" in studio_text
    assert "function nextInsertSlotId(track)" in studio_text
    assert "function insertSlotNumber(slotId)" in studio_text
    assert "return `insert_${nextNumber}`" in studio_text
    assert "function uniqueMixerPluginLabel(track, slot)" in studio_text
    assert (
        "return duplicateIndex === 0 ? baseLabel : `${baseLabel} (${duplicateIndex})`"
        in studio_text
    )
    assert "function mixerSendRows(track)" in studio_text
    assert "function updateTrackSend(track, index, patch)" in studio_text
    assert "function addTrackSend(track, targetBusId)" in studio_text
    assert "function removeTrackSend(track, index)" in studio_text
    assert (
        "@contextmenu.prevent=\"openAutomationMenu($event, automationTargetForTrackPan(track), "
        "`${track.name} Pan`)\""
    ) in studio_text
    assert (
        "@contextmenu.prevent=\"openAutomationMenu($event, automationTargetForTrackVolume(track), "
        "`${track.name} Volume`)\""
    ) in studio_text
    assert ".mixer-pan-center-line" in studio_text


def test_music_studio_mixer_docks_master_and_preserves_pan_fader_space():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="mixer-strip-body"' in studio_text
    assert 'class="mixer-track-strip-scroll"' in studio_text
    assert 'class="mixer-master-dock"' in studio_text
    assert (
        ".mixer-strip-body {\n"
        "  flex: 1 1 auto;\n"
        "  min-height: 0;\n"
        "  min-width: 0;\n"
        "  display: grid;\n"
        "  grid-template-columns: minmax(0, 1fr) 154px;"
    ) in studio_text
    assert (
        ".mixer-track-strip-scroll {\n"
        "  min-height: 0;\n"
        "  min-width: 0;\n"
        "  overflow: auto;"
    ) in studio_text
    assert (
        ".mixer-strip {\n"
        "  width: 154px;\n"
        "  min-width: 154px;\n"
        "  height: 100%;\n"
        "  min-height: 0;\n"
        "  display: grid;\n"
        "  grid-template-rows: auto minmax(48px, 1fr) auto minmax(40px, auto) auto 132px;"
    ) in studio_text
    assert ".mixer-pan {\n  min-height: 40px;" in studio_text
    assert ".mixer-fader {\n  min-height: 132px;" in studio_text
    assert (
        ".master-strip {\n"
        "  height: 100%;\n"
        "  grid-template-rows: auto minmax(48px, 1fr) minmax(40px, auto) auto 132px;"
        in studio_text
    )


def test_music_studio_master_bus_uses_editable_strip_controls_without_sends():
    studio_text = _read(STUDIO_COMPONENT)

    assert (
        "const masterBus = computed(() => normalizeMasterBus(project.value?.master_bus))"
        in studio_text
    )
    assert "function normalizeMasterBus(bus = {})" in studio_text
    assert "function updateMasterBus(patch)" in studio_text
    assert "function setMasterBusPlugin(plugin, slotId)" in studio_text
    assert "async function onMasterBusPluginSelect(slotId, value)" in studio_text
    assert "{{ masterBus.name }}" in studio_text
    assert ":style=\"{ background: masterBus.color }\"" in studio_text
    assert 'v-for="slot in mixerInsertSlots(masterBus)"' in studio_text
    assert "@change=\"onMasterBusPluginSelect(slot.id, $event.target.value)\"" in studio_text
    assert '@change="updateMasterBus({ pan: Number($event.target.value) })"' in studio_text
    assert '@click.stop="updateMasterBus({ mute: !masterBus.mute })"' in studio_text
    assert '@click.stop="updateMasterBus({ solo: !masterBus.solo })"' in studio_text
    assert '@change="updateMasterBus({ volume: Number($event.target.value) })"' in studio_text
    assert 'class="mixer-master-body"' not in studio_text


def test_music_studio_automation_tracks_can_be_drawn_like_controller_lanes():
    studio_text = _read(STUDIO_COMPONENT)

    assert "startAutomationDrag(track, point, event.pointerId)" in studio_text
    assert "function onAutomationPointerMove(event)" in studio_text
    assert "function onAutomationPointerUp()" in studio_text
    assert "writeAutomationDragPoints(" in studio_text
    assert "quantizedBeatsBetween(startBeat, endBeat, activePianoSnapStep.value)" in studio_text
    assert (
        "await persistAutomationTrackPoints(drag.trackId, automationTrackPoints(drag.trackId))"
        in studio_text
    )
    assert "function automationValueFromY(track, y)" in studio_text
    assert "function automationPointY(track, point, trackIndex)" in studio_text


def test_music_studio_exposes_automation_parameter_picker_and_learned_list():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert '<option value="automation">' in studio_text
    assert "v-if=\"trackCreateType === 'automation'\"" in studio_text
    assert "openAutomationParameterPickerForCreate" in studio_text
    assert "openAutomationParameterPickerForTrack(track)" in studio_text
    assert "automation-parameter-dialog" in studio_text
    assert "defaultAutomationTargets" in studio_text
    assert "learnedAutomationTargets" in studio_text
    assert "automationTargetForTempoBpm()" in studio_text
    assert "automationTargetForTimeSignatureNumerator()" in studio_text
    assert 'key: "global-tempo-bpm"' in studio_text
    assert 'key: "global-time-signature-numerator"' in studio_text
    assert (
        '@contextmenu.prevent="openAutomationMenu($event, automationTargetForTempoBpm(), '
        "'Tempo BPM')"
    ) in studio_text
    assert (
        '@contextmenu.prevent.stop="openAutomationMenu($event, '
        "automationTargetForTimeSignatureNumerator(), 'Time Signature Numerator')"
    ) in studio_text
    assert 'class="automation-learned-row"' in studio_text
    assert '@click="bindAutomationPickerTarget(item.target)"' in studio_text
    assert '@keydown.enter.stop.prevent="bindAutomationPickerTarget(item.target)"' in studio_text
    assert "@pointerdown.stop" in studio_text
    assert "@click.stop" in studio_text
    assert "renameLearnedAutomationParameter(item.id, $event.target.value)" in studio_text
    assert "bindAutomationPickerTarget(target)" in studio_text
    assert "Bind" not in studio_text
    assert "target?.kind === 'tempo_bpm'" in studio_text
    assert "target?.kind === 'time_signature_numerator'" in studio_text
    assert "if (track?.target?.kind === 'time_signature_numerator')" in studio_text
    assert "pollCapturedPluginParameters" in studio_text
    assert "async function pollCapturedPluginParameters()" in host_text
    assert "async function renameLearnedAutomationParameter(id, name)" in host_text
    assert "studioCapturedPluginParameters: ()" in api_text
    assert "studioRenameLearnedPluginParameter: (id, name)" in api_text


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
