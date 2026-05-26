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


def test_music_studio_supports_audio_export_dialog():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)
    api_text = _read(API)

    assert '@click="openExportDialog"' in studio_text
    assert 'v-if="exportDialogOpen"' in studio_text
    assert 'class="export-dialog"' in studio_text
    assert 'v-model="exportTarget"' in studio_text
    assert 'value="entire_project"' in studio_text
    assert 'value="selected_tracks"' in studio_text
    assert 'v-model="exportMode"' in studio_text
    assert 'value="mixdown"' in studio_text
    assert 'value="stems"' in studio_text
    assert 'v-model="exportFormat"' in studio_text
    assert 'value="wav"' in studio_text
    assert 'value="flac"' in studio_text
    assert 'value="mp3"' in studio_text
    assert 'v-model.number="exportSampleRate"' in studio_text
    assert 'v-model="exportBitDepth"' in studio_text
    assert 'v-model="exportBitrate"' in studio_text
    assert '@click="exportCurrentAudio"' in studio_text
    assert "async function exportCurrentAudio()" in studio_text
    assert "exportAudio({" in studio_text
    assert "async function exportAudio(payload)" in host_text
    assert "exporting," in host_text
    assert "studioExportAudio: (payload)" in api_text
    assert "/api/music/studio/export" in api_text


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


def test_music_studio_topbar_removes_manual_sync_and_demo_controls():
    studio_text = _read(STUDIO_COMPONENT)

    assert '@click="syncProject({ broadcast: true })"' not in studio_text
    assert '@click="resetDemo()"' not in studio_text
    assert "  syncProject,\n" not in studio_text
    assert "  resetDemo,\n" not in studio_text


def test_music_studio_timeline_toolbar_matches_piano_editor_tools():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="timeline-actions arrangement-actions"' in studio_text
    assert 'class="timeline-control piano-quantize"' in studio_text
    assert 'title="选择时间线量化网格"' in studio_text
    assert 'title="MIDI 写入是否吸附到当前量化"' in studio_text
    assert 'title="创建全局小轨道"' in studio_text
    assert ":class=\"['mini-btn text', { active: timelineTool === 'select' }]\"" in studio_text
    assert ":class=\"['mini-btn text', { active: timelineTool === 'draw' }]\"" in studio_text
    assert "function setTimelineTool(tool)" in studio_text
    assert "async function drawTimelineMidiAtPoint(point)" in studio_text
    assert "await createMidiClipAtBeat(track.id, point.beat)" in studio_text
    assert 'title="Create MIDI clip at playhead"' not in studio_text
    assert 'title="Create audio clip placeholder at playhead"' not in studio_text
    assert 'title="Copy selected clips"' not in studio_text
    assert 'title="Paste clips at playhead"' not in studio_text
    assert 'title="Delete selected clips"' in studio_text


def test_music_studio_piano_toolbar_removes_visible_copy_paste_and_clear_buttons():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'title="Copy selected notes"' not in studio_text
    assert 'title="Paste copied notes at the playhead"' not in studio_text
    assert 'title="Clear selected MIDI clip"' not in studio_text
    assert 'title="Write C minor figure"' not in studio_text
    assert "function copySelectedNotes()" in studio_text
    assert "async function pasteNotes()" in studio_text
    assert "copySelectedNotes()" in studio_text
    assert "pasteNotes()" in studio_text


def test_music_studio_arrangement_displays_project_level_piano_subtracks():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const arrangementVisibleSubtracks = computed(" in studio_text
    assert "function arrangementSubtrackTop(subtrackId)" in studio_text
    assert "function arrangementTrackTop(trackIndex)" in studio_text
    assert "function drawArrangementMeterLane(ctx, width, top)" in studio_text
    assert "function drawArrangementHarmonyLane(ctx, width, top)" in studio_text
    assert "drawArrangementMeterLane(ctx, width, arrangementSubtrackTop('meter'))" in studio_text
    assert (
        "drawArrangementHarmonyLane(ctx, width, arrangementSubtrackTop('harmony'))" in studio_text
    )
    assert "arrangementTrackTop(index)" in studio_text
    assert "arrangementTrackTop(trackIndex)" in studio_text


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


def test_music_studio_beat_numbering_uses_meter_event_positions():
    """Position and ruler labels use the same meter event helper so 6/8 and
    later meter changes count beat-within-bar from the active meter segment."""
    studio_text = _read(STUDIO_COMPONENT)

    assert "meterPositionAtBeat(project.value, visualPositionBeats.value)" in studio_text
    assert "meterPositionAtBeat(project.value, absoluteBeat)" in studio_text
    assert "effectiveMeterAtBeat(project.value, absoluteBeat)" in studio_text


def test_music_studio_arrangement_grid_follows_meter_events():
    """The main arrangement grid must use the same meter event map as the
    piano meter lane, so bar lines move after a 3/4 or 5/8 marker."""
    studio_text = _read(STUDIO_COMPONENT)

    assert "function paintGrid(ctx, width, height, offsetX, offsetY)" in studio_text
    assert "for (const line of meterBarLinesBetween(project.value, 0, beats))" in studio_text
    assert "const barX = offsetX + line.beat * scale" in studio_text
    assert "for (let bar = 0; bar * barLen <= beats; bar++)" not in studio_text
    # paintControllerGrid still overlays clip-local bar lines.
    assert "barLen * pianoPxPerBeat.value" in studio_text
    assert (
        "for (const line of meterBarLinesBetween(project.value, clipStart, endBeat))"
    ) in studio_text


def test_music_studio_piano_and_arrangement_rulers_share_decimal_beat_labels():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function rulerBeatLabel(absoluteBeat)" in studio_text
    assert "function drawBeatRulerLabels(ctx, {" in studio_text
    assert (
        "return position.beat === 1 ? String(position.bar) : `${position.bar}.${position.beat}`"
    ) in studio_text
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


def test_music_studio_arrangement_ruler_and_subtracks_stay_sticky_while_tracks_scroll():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'ref="arrangementHeaderCanvas"' in studio_text
    assert 'class="editor-canvas arrangement-header-canvas"' in studio_text
    assert 'class="arrangement-timeline-stack"' in studio_text
    assert 'class="track-list-sticky-header"' in studio_text
    assert "const arrangementHeaderCanvas = ref(null)" in studio_text
    assert "function drawArrangementHeader(ctx, width)" in studio_text
    assert "function drawArrangementBody(ctx, width, height)" in studio_text
    assert "drawArrangementHeader(headerCtx, width)" in studio_text
    assert "drawArrangementBody(bodyCtx, width, bodyHeight)" in studio_text
    assert "function arrangementCanvasForEvent(event)" in studio_text
    assert "if (canvas === arrangementCanvas.value) y += arrangementTrackTop(0)" in studio_text
    assert ".arrangement-header-canvas {" in studio_text
    assert ".track-list-sticky-header {" in studio_text
    assert "position: sticky;" in studio_text
    assert "top: 0;" in studio_text
    assert "z-index: 5;" in studio_text


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


def test_music_studio_piano_roll_extends_from_low_c_to_c9():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const minPitch = 0" in studio_text
    assert "const maxPitch = 120" in studio_text
    assert "const bodyHeight = (maxPitch - minPitch + 1) * pianoRowH" in studio_text
    assert "const pitch = clamp(maxPitch - row, minPitch, maxPitch)" in studio_text
    assert "pitch: clamp(note.pitch + deltaPitch, minPitch, maxPitch)" in studio_text


def test_music_studio_piano_ruler_and_subtracks_stay_sticky_while_notes_scroll():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'ref="pianoHeaderCanvas"' in studio_text
    assert 'class="editor-canvas piano-header-canvas"' in studio_text
    assert 'class="piano-scroll-content"' in studio_text
    assert "const pianoHeaderCanvas = ref(null)" in studio_text
    assert "function drawPianoHeader(ctx, width, clip)" in studio_text
    assert "function drawPianoBody(ctx, width, height, clip)" in studio_text
    assert "drawPianoHeader(headerCtx, width, clip)" in studio_text
    assert "drawPianoBody(bodyCtx, width, bodyHeight, clip)" in studio_text
    assert "function pianoCanvasForEvent(event)" in studio_text
    assert "if (canvas === pianoCanvas.value) y += pianoNoteTop.value" in studio_text
    assert ".piano-header-canvas {" in studio_text
    assert "position: sticky;" in studio_text
    assert "top: 0;" in studio_text
    assert "z-index: 3;" in studio_text


def test_music_studio_recenters_piano_viewport_when_opening_or_switching_clips():
    studio_text = _read(STUDIO_COMPONENT)

    assert "pianoScrollTopForNotes" in studio_text
    assert "function focusPianoViewport()" in studio_text
    assert "function schedulePianoViewportFocus()" in studio_text
    assert "requestAnimationFrame(() => focusPianoViewport())" in studio_text
    assert (
        "function openPiano() {\n"
        "  lowerEditorMode.value = 'piano'\n"
        "  schedulePianoViewportFocus()\n"
        "}" in studio_text
    )
    assert "watch(activeClipId, () => {" in studio_text
    assert (
        "if (pianoVisible.value && activeMidiClip.value) schedulePianoViewportFocus()"
        in studio_text
    )


def test_music_studio_mixer_window_replaces_inspector_rack():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="mixer-panel"' in studio_text
    assert ":class=\"['mixer-strip', { active: activeTrack?.id === track.id }]\"" in studio_text
    assert 'v-for="track in mixerTracks"' in studio_text
    assert (
        "const mixerTracks = computed(() => tracks.value.filter("
        "track => !isAutomationTrack(track)))" in studio_text
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
        '@contextmenu.prevent="openAutomationMenu($event, automationTargetForTrackPan(track), '
        '`${track.name} Pan`)"'
    ) in studio_text
    assert (
        '@contextmenu.prevent="openAutomationMenu($event, automationTargetForTrackVolume(track), '
        '`${track.name} Volume`)"'
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
        ".mixer-track-strip-scroll {\n  min-height: 0;\n  min-width: 0;\n  overflow: auto;"
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
        "  grid-template-rows: auto minmax(48px, 1fr) minmax(40px, auto) auto 132px;" in studio_text
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
    assert ':style="{ background: masterBus.color }"' in studio_text
    assert 'v-for="slot in mixerInsertSlots(masterBus)"' in studio_text
    assert '@change="onMasterBusPluginSelect(slot.id, $event.target.value)"' in studio_text
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
    assert "snapBeats: drag.type !== 'automation-curve'" in studio_text
    assert "function automationValueFromY(track, y)" in studio_text
    assert "function automationPointY(track, point, trackIndex)" in studio_text


def test_music_studio_automation_points_can_be_selected_and_dragged_without_redrawing():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const selectedAutomationPoint = ref({ trackId: null, index: -1 })" in studio_text
    assert (
        "const hit = hitTestAutomationPoint(track, point.x, point.y, point.trackIndex)"
        in studio_text
    )
    assert "startAutomationPointDrag(track, hit.index, event.pointerId)" in studio_text
    assert "type: 'automation-point'" in studio_text
    assert "moveAutomationPoint(track, automationDrag.pointIndex, beat, value)" in studio_text
    assert "function drawAutomationHoldLine(ctx, track, points, trackIndex, right)" in studio_text


def test_music_studio_controller_events_can_be_selected_and_dragged_without_redrawing():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const selectedControllerEventId = ref(null)" in studio_text
    assert "const hit = hitTestControllerEvent(definition, point.x, point.y)" in studio_text
    assert "type: 'event-point'" in studio_text
    assert (
        "updateControllerEvent(controllerDrag.definition, controllerDrag.eventId, { beat, value })"
        in studio_text
    )
    assert "if (point.synthetic) continue" in studio_text


def test_music_studio_controller_events_have_draggable_curve_handles():
    studio_text = _read(STUDIO_COMPONENT)

    assert "controllerCurveHandleHitRadius" in studio_text
    assert "curveHandleMinSegmentPx" in studio_text
    assert "function drawControllerCurvePath(ctx, points, definition)" in studio_text
    assert (
        "function drawControllerCurveHandles(ctx, points, definition, colorStyles)" in studio_text
    )
    assert "function hitTestControllerCurveHandle(definition, x, y)" in studio_text
    controller_start = studio_text.index("function onControllerLanePointerDown")
    controller_point_hit = studio_text.index(
        "const hit = hitTestControllerEvent(definition, point.x, point.y)",
        controller_start,
    )
    controller_curve_hit = studio_text.index(
        "const curveHit = hitTestControllerCurveHandle(definition, point.x, point.y)",
        controller_start,
    )
    assert controller_point_hit < controller_curve_hit
    assert (
        "const curveHit = hitTestControllerCurveHandle(definition, point.x, point.y)" in studio_text
    )
    assert "type: 'event-curve'" in studio_text
    assert "function updateControllerEventCurve(eventId, curveAmount)" in studio_text
    assert "updateControllerEventCurve(controllerDrag.eventId, nextCurveAmount)" in studio_text
    assert "return applyCurveAmount(event, curveAmount)" in studio_text
    assert (
        "if ((endBeat - startBeat) * pianoPxPerBeat.value < curveHandleMinSegmentPx) return null"
        in studio_text
    )


def test_music_studio_automation_points_have_draggable_curve_handles():
    studio_text = _read(STUDIO_COMPONENT)

    assert "automationCurveHandleHitRadius" in studio_text
    assert "curveHandleMinSegmentPx" in studio_text
    assert "function automationCurveValueAtBeat(track, left, right, beat)" in studio_text
    assert (
        "function drawAutomationSegmentPath(ctx, track, points, trackIndex, right)" in studio_text
    )
    assert "function drawAutomationCurveHandles(ctx, track, points, trackIndex)" in studio_text
    assert "function hitTestAutomationCurveHandle(track, x, y, trackIndex)" in studio_text
    automation_start = studio_text.index("if (isAutomationTrack(track) && point) {")
    automation_point_hit = studio_text.index(
        "const hit = hitTestAutomationPoint(track, point.x, point.y, point.trackIndex)",
        automation_start,
    )
    automation_curve_hit = studio_text.index(
        "const curveHit = hitTestAutomationCurveHandle(track, point.x, point.y, point.trackIndex)",
        automation_start,
    )
    assert automation_point_hit < automation_curve_hit
    assert (
        "const curveHit = hitTestAutomationCurveHandle(track, point.x, point.y, point.trackIndex)"
        in studio_text
    )
    assert "type: 'automation-curve'" in studio_text
    assert (
        "updateAutomationPointCurve(track, automationDrag.pointIndex, nextCurveAmount)"
        in studio_text
    )
    assert "points[pointIndex] = applyCurveAmount(points[pointIndex], curveAmount)" in studio_text
    assert ".map(point => normalizeAutomationPoint(track, point, options))" in studio_text
    assert (
        "if ((endBeat - startBeat) * arrangementPxPerBeat.value "
        "< curveHandleMinSegmentPx) return null" in studio_text
    )


def test_music_studio_piano_roll_has_dedicated_meter_lane():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="piano-subtrack-select"' in studio_text
    assert "pianoSubtrackCreateValue" in studio_text
    assert "createPianoSubtrack()" in studio_text
    assert "const pianoMeterLaneVisible = computed(" in studio_text
    assert "const pianoMeterLaneH = 28" in studio_text
    assert "const pianoMeterLaneTop = computed(" in studio_text
    assert "if (point.meterLane) {" in studio_text
    assert "function drawPianoMeterLane(ctx, width, clip)" in studio_text
    assert "meterBarLinesBetween(project.value, clipStart, endBeat)" in studio_text
    assert "await upsertMeterEventAtBeat(point.beat)" in studio_text
    assert "upsertMeterEventInProject(nextProject, nextBeat, numerator, denominator)" in studio_text


def test_music_studio_piano_meter_lane_can_collapse_without_deleting_events():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'class="piano-meter-toggle"' in studio_text
    assert 'title="收起拍号轨"' in studio_text
    assert '@click.stop="togglePianoMeterLane"' in studio_text
    assert ".piano-meter-toggle {" in studio_text
    assert "right: 10px;" in studio_text
    assert ".piano-meter-toggle::before {" in studio_text
    assert "const pianoMeterLaneOpen = ref(false)" in studio_text
    assert "const hasProjectMeterEvents = computed(" in studio_text
    assert "const pianoMeterLaneVisible = computed(() => pianoMeterLaneOpen.value)" in studio_text
    assert "{ id: 'meter', label: '拍号轨', disabled: pianoMeterLaneVisible.value }" in studio_text
    assert "label: '收起拍号轨'" not in studio_text
    assert "await togglePianoMeterLane()" in studio_text
    assert "function togglePianoMeterLane()" in studio_text
    assert "pianoMeterLaneOpen.value = false" in studio_text
    assert "pianoMeterLaneOpen.value = true" in studio_text
    assert "if (!hasProjectMeterEvents.value)" in studio_text


def test_music_studio_piano_harmony_lane_persists_agent_visible_markers():
    studio_text = _read(STUDIO_COMPONENT)

    assert (
        "{ id: 'harmony', label: '和声轨', disabled: pianoHarmonyLaneVisible.value }" in studio_text
    )
    assert "const pianoSubtrackOrder = ref([])" in studio_text
    assert "const pianoVisibleSubtracks = computed(" in studio_text
    assert "normalizePianoSubtrackOrder(nextProject?.piano_subtrack_order)" in studio_text
    assert "nextProject.piano_subtrack_order = normalizePianoSubtrackOrder(" in studio_text
    assert 'class="piano-harmony-popover"' in studio_text
    assert 'v-model="pianoHarmonyEditor.text"' in studio_text
    assert "function drawPianoHarmonyLane(ctx, width, clip, top)" in studio_text
    assert "function hitTestHarmonyEvent(point)" in studio_text
    assert "function openPianoHarmonyEditor(event, harmonyHit)" in studio_text
    assert "async function applyPianoHarmonyEditor()" in studio_text
    assert "function normalizeEditableHarmonyEvents(events)" in studio_text
    assert "nextProject.harmony_events = normalizeEditableHarmonyEvents(events)" in studio_text


def test_music_studio_piano_subtracks_resync_when_project_subtrack_data_changes():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const pianoSubtrackSyncKey = ref('')" in studio_text
    assert "function pianoSubtrackProjectSyncKey(nextProject)" in studio_text
    assert "function syncPianoSubtrackLanes(nextProject)" in studio_text
    assert "if (pianoSubtrackSyncKey.value === syncKey) return" in studio_text
    assert "pianoSubtrackSyncKey.value = syncKey" in studio_text
    assert "syncPianoSubtrackLanes(nextProject)" in studio_text
    assert "pianoMeterLaneOpenSynced" not in studio_text


def test_music_studio_piano_meter_label_opens_editor_and_playhead_uses_visible_piano_length():
    studio_text = _read(STUDIO_COMPONENT)

    assert 'ref="pianoMeterEditorRoot"' in studio_text
    assert 'class="piano-meter-popover"' in studio_text
    assert "openPianoMeterEditor(event, meterHit)" in studio_text
    assert "isMeterEventLabelHit(point, meterHit)" in studio_text
    assert "applyPianoMeterEditor()" in studio_text
    assert "const visibleLength = pianoLengthBeats(clip)" in studio_text
    assert "localBeat > visibleLength" in studio_text


def test_music_studio_top_right_meter_change_writes_event_at_cursor_or_global_at_zero():
    studio_text = _read(STUDIO_COMPONENT)

    assert "function isAtTimelineStart(beat)" in studio_text
    assert "async function applyTransportTimeSignatureChange()" in studio_text
    assert "if (isAtTimelineStart(changeBeat))" in studio_text
    assert "nextProject.time_signature = [numerator, denominator]" in studio_text
    assert "ensureBaseMeterEventIfNeeded(nextProject, changeBeat)" in studio_text
    assert (
        "upsertMeterEventInProject(nextProject, changeBeat, numerator, denominator)" in studio_text
    )
    assert "baseMeterEvent(nextProject)" in studio_text


def test_music_studio_piano_ruler_uses_meter_segments_for_fractional_beat_ticks():
    studio_text = _read(STUDIO_COMPONENT)

    assert "meterSegments(project.value, clipStart, endBeat)" in studio_text
    assert "firstMultipleAtOrAfter(segment.start, unit, segment.anchor)" in studio_text
    assert "if (unit !== 1" not in studio_text


def test_music_studio_draw_mode_existing_notes_and_meter_events_use_click_delete_long_press_drag():
    studio_text = _read(STUDIO_COMPONENT)

    assert "const pianoDrawLongPressMs = 260" in studio_text
    assert "startDrawNotePress(event, point, hit)" in studio_text
    assert "startDrawMeterEventPress(event, point, meterHit)" in studio_text
    assert "type: 'draw-note-press'" in studio_text
    assert "type: 'draw-meter-event-press'" in studio_text
    assert "schedulePianoLongPress(() => activateDrawNotePressDrag())" in studio_text
    assert "schedulePianoLongPress(() => activateDrawMeterEventPressDrag())" in studio_text
    assert "await deletePianoNoteById(drag.noteId)" in studio_text
    assert "await deleteMeterEventAtIndex(drag.eventIndex)" in studio_text
    assert "type: 'meter-event-move'" in studio_text
    assert "await persistMeterEvents(project.value.meter_events || [])" in studio_text


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
    assert 'key: "global-tempo-bpm"' in studio_text
    assert (
        '@contextmenu.prevent="openAutomationMenu($event, automationTargetForTempoBpm(), '
        "'Tempo BPM')"
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
    assert "time_signature_numerator" not in studio_text
    assert "pollCapturedPluginParameters" in studio_text
    assert "async function pollCapturedPluginParameters()" in host_text
    assert "async function renameLearnedAutomationParameter(id, name)" in host_text
    assert "studioCapturedPluginParameters: ()" in api_text
    assert "studioRenameLearnedPluginParameter: (id, name)" in api_text


def test_music_studio_frontend_transport_uses_tempo_automation_and_meter_events():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)

    assert "secondsToBeats(project.value, positionSeconds.value)" in host_text
    assert "beatsToSeconds(project.value, nextBeat)" in studio_text
    assert "effectiveTempoAtBeat(project.value, visualPositionBeats.value) / 60" in studio_text
    assert "effectiveMeterAtBeat(nextProject, visualPositionBeats.value)" in studio_text
    assert "syncTransportDisplayFields(project.value)" in studio_text


def test_music_studio_separates_host_audio_ws_and_pcm_streaming_statuses():
    studio_text = _read(STUDIO_COMPONENT)
    host_text = _read(DAW_HOST)

    assert "const hostStreamingEnabled = ref(false)" in host_text
    assert "const pcmStreaming = ref(false)" in host_text
    assert "let audioContext = null" in host_text
    assert "async function ensurePcmPlayer()" in host_text
    assert "new URL('../worklets/pcm-player-worklet.js', import.meta.url)" in host_text
    assert "new AudioWorkletNode(audioContext, 'atri-pcm-player'" in host_text
    assert "playerNode.connect(audioContext.destination)" in host_text
    assert "hostStreamingEnabled.value = res.engine.streaming_enabled === true" in host_text
    assert "hostStreamingEnabled.value = false" in host_text
    assert "markPcmStreaming()" in host_text
    assert "clearPcmStreaming()" in host_text
    assert "closePcmPlayer()" in host_text
    assert "hostStreamingEnabled," in host_text
    assert "pcmStreaming," in host_text
    assert "connectAudioStream()" in studio_text
    assert "disconnectAudioStream()" in studio_text
    assert "hostStreamingEnabled," in studio_text
    assert "Audio WS Connected" in studio_text
    assert "PCM Streaming" in studio_text
    assert "Host Online" in studio_text
    assert "{{ hostStreamingEnabled ? 'enabled' : 'disabled' }}" in studio_text
    assert "{{ audioConnected ? 'connected' : 'disconnected' }}" in studio_text
    assert "{{ pcmStreaming ? 'streaming' : 'idle' }}" in studio_text
    audio_message_start = host_text.index("audioWs.onmessage = async (event) => {")
    ensure_player = host_text.index("await ensurePcmPlayer()", audio_message_start)
    player_guard = host_text.index("if (!playerNode) return", audio_message_start)
    player_post = host_text.index("playerNode.port.postMessage(", audio_message_start)
    pcm_mark = host_text.index("markPcmStreaming()", audio_message_start)
    assert ensure_player < player_guard < player_post < pcm_mark


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
