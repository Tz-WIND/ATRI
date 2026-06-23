<template>
  <div
    :class="[
      'studio-page',
      {
        embedded,
        'inspector-hidden': !inspectorVisible,
        'piano-closed': !lowerEditorVisible,
      },
    ]"
    tabindex="0"
    @keydown="onStudioKeydown"
  >
    <StudioTopbar
      v-model:project-copy-title="projectCopyTitle"
      v-model:tempo-input="tempoInput"
      v-model:tempo-input-focused="tempoInputFocused"
      v-model:time-signature-numerator="timeSignatureNumerator"
      v-model:time-signature-numerator-focused="timeSignatureNumeratorFocused"
      v-model:time-signature-denominator-popover-open="timeSignatureDenominatorPopoverOpen"
      v-model:inspector-visible="inspectorVisible"
      :embedded="embedded"
      :project="project"
      :project-archives="projectArchives"
      :active-project-id="activeProjectId"
      :project-library-open="projectLibraryOpen"
      :loading="loading"
      :playing="playing"
      :position-label="positionLabel"
      :time-signature-denominator="timeSignatureDenominator"
      :time-signature-label="timeSignatureLabel"
      :time-signature-denominator-label="timeSignatureDenominatorLabel"
      :time-signature-denominator-options="timeSignatureDenominatorOptions"
      :time-signature-popover-open="timeSignaturePopoverOpen"
      :host="host"
      :audio-connected="audioConnected"
      :host-streaming-enabled="hostStreamingEnabled"
      :pcm-streaming="pcmStreaming"
      :mixer-visible="mixerVisible"
      :exporting="exporting"
      :archive-time-label="archiveTimeLabel"
      :denominator-label="denominatorLabel"
      :set-time-signature-root="setTimeSignatureRoot"
      @toggle-project-library="toggleProjectLibrary"
      @save-copy="saveCurrentProjectCopy"
      @open-archive="openArchivedProject"
      @toggle-play="togglePlay"
      @stop-playback="stopPlayback"
      @sync-tempo-field="syncTempoField(project)"
      @update-tempo="updateTempo"
      @tempo-wheel="onTempoWheel"
      @tempo-context-menu="event => openAutomationMenu(event, automationTargetForTempoBpm(), 'Tempo BPM')"
      @toggle-time-signature-popover="toggleTimeSignaturePopover"
      @sync-time-signature-fields="syncTimeSignatureFields(project)"
      @update-time-signature="updateTimeSignature"
      @set-time-signature-denominator="setTimeSignatureDenominator"
      @open-mixer="openMixer"
      @open-export="openExportDialog"
    />
    <div
      v-if="hostError"
      class="studio-error"
    >
      {{ hostError }}
    </div>

    <main class="studio-body">
      <section
        ref="editorStack"
        class="editor-stack"
        :style="editorStackStyle"
      >
        <ArrangementEditorPanel
          ref="arrangementEditorPanel"
          :layout="arrangementLayoutContext"
          :toolbar="arrangementToolbarContext"
          :track-list="arrangementTrackListContext"
          :audio-drop="arrangementAudioDropContext"
          :context-menus="arrangementContextMenuContext"
          @add-track="openTrackCreateDialog"
          @toggle-timeline-quantize-menu="timelineQuantizeMenuOpen = !timelineQuantizeMenuOpen"
          @set-piano-quantize-option="setPianoQuantizeOption"
          @toggle-piano-snap="pianoSnapEnabled = !pianoSnapEnabled"
          @update-piano-subtrack-create-value="value => { pianoSubtrackCreateValue = value }"
          @create-piano-subtrack="createPianoSubtrack"
          @set-timeline-tool="setTimelineTool"
          @delete-selected-clips="deleteSelectedClips"
          @start-track-list-resize="startTrackListResize"
          @audio-drag-enter="onAudioDragEnter"
          @audio-drag-over="onAudioDragOver"
          @audio-drag-leave="onAudioDragLeave"
          @audio-drop="onAudioDrop"
          @scroll="syncArrangementScroll"
          @select-track="selectTrack"
          @open-context-menu="openTrackContextMenu"
          @start-reorder="startTrackReorderDrag"
          @reorder-over="onTrackReorderDragOver"
          @drop-reorder="dropTrackReorder"
          @end-reorder="endTrackReorder"
          @row-keydown="onTrackRowKeydown"
          @plugin-select="onPluginSelect"
          @update-track-output-bus="updateTrackOutputBus"
          @toggle-plugin-editor="togglePluginEditor"
          @update-track="updateTrack"
          @open-automation-picker="openAutomationParameterPickerForTrack"
          @arrangement-pointer-down="onArrangementPointerDown"
          @arrangement-wheel="onArrangementWheel"
          @arrangement-double-click="onArrangementDoubleClick"
          @confirm-create-automation="confirmCreateAutomationFromMenu"
          @delete-track-from-context-menu="deleteTrackFromContextMenu"
        />

        <div
          v-if="pianoVisible && activeMidiClip"
          ref="lowerEditorPanel"
          class="piano-panel"
        >
          <div
            class="piano-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            title="Resize piano roll"
            @pointerdown="startLowerEditorResize"
          >
            <span />
          </div>
          <PianoEditorHeader
            v-model:subtrack-create-value="pianoSubtrackCreateValue"
            :clip-name="activeMidiClip.clip.name"
            :quantize-menu-open="pianoQuantizeMenuOpen"
            :quantize-label="pianoQuantizeLabel"
            :quantize-options="pianoQuantizeOptions"
            :quantize-id="pianoQuantizeId"
            :snap-active="isPianoSnapActive"
            :subtrack-options="pianoSubtrackOptions"
            :tool="pianoTool"
            :selected-note-count="selectedNoteIds.size"
            @toggle-quantize-menu="pianoQuantizeMenuOpen = !pianoQuantizeMenuOpen"
            @set-quantize-option="setPianoQuantizeOption"
            @toggle-snap="pianoSnapEnabled = !pianoSnapEnabled"
            @create-subtrack="createPianoSubtrack"
            @set-tool="value => { pianoTool = value }"
            @delete-selected-notes="deleteSelectedNotes"
            @close="closePiano"
          />
          <div
            ref="pianoWorkspace"
            class="piano-workspace"
          >
            <div
              ref="pianoWrap"
              class="piano-canvas-wrap"
              @scroll="syncPianoScroll('piano')"
            >
              <canvas
                ref="pianoHeaderCanvas"
                class="editor-canvas piano-header-canvas"
                @pointerdown="onPianoPointerDown"
                @wheel="onPianoWheel"
                @contextmenu.prevent
              />
              <div class="piano-scroll-content">
                <canvas
                  ref="pianoCanvas"
                  class="editor-canvas"
                  @pointerdown="onPianoPointerDown"
                  @wheel="onPianoWheel"
                  @contextmenu.prevent
                />
              </div>
            </div>
            <button
              v-if="pianoMeterLaneVisible"
              class="piano-meter-toggle"
              type="button"
              title="收起拍号轨"
              aria-label="收起拍号轨"
              :style="{ top: `${pianoMeterLaneTop + 4}px` }"
              @pointerdown.stop
              @click.stop="togglePianoMeterLane"
            />
            <button
              v-if="pianoHarmonyLaneVisible"
              class="piano-harmony-toggle"
              type="button"
              title="收起和声轨"
              aria-label="收起和声轨"
              :style="{ top: `${pianoHarmonyLaneTop + 4}px` }"
              @pointerdown.stop
              @click.stop="togglePianoHarmonyLane"
            />
            <ControllerLanesPanel
              v-if="controllerLanes.length"
              v-model:custom-controller-number="customControllerNumber"
              :lanes="controllerLanes"
              :panel-height="controllerPanelHeight"
              :timeline-width="pianoTimelineWidth"
              :piano-key-width="pianoKeyW"
              :scroll-left="controllerScrollLeft"
              :menu-lane-id="controllerMenuLaneId"
              :axis-top="controllerAxisTop"
              :axis-middle="controllerAxisMiddle"
              :axis-bottom="controllerAxisBottom"
              :controller-label="controllerLabel"
              :menu-options="controllerMenuOptions"
              :set-wrap="setControllerWrap"
              :set-canvas="setControllerLaneCanvas"
              @scroll="syncPianoScroll('controller')"
              @toggle-menu="toggleControllerMenu"
              @set-lane-controller="setLaneController"
              @remove-lane="removeControllerLane"
              @add-controller="addControllerToLane"
              @add-custom-controller="addCustomControllerToLane"
              @remove-active-controller="removeActiveControllerFromLane"
              @lane-pointerdown="onControllerLanePointerDown"
              @add-lane="addControllerLane"
            />
            <div
              v-if="pianoMeterEditor.open"
              ref="pianoMeterEditorRoot"
              class="piano-meter-popover"
              :style="{ left: `${pianoMeterEditor.x}px`, top: `${pianoMeterEditor.y}px` }"
              @pointerdown.stop
              @click.stop
            >
              <label>
                <span>拍号</span>
                <input
                  v-model.number="pianoMeterEditor.numerator"
                  type="number"
                  min="1"
                  max="255"
                  step="1"
                  @change="applyPianoMeterEditor()"
                  @keydown.enter.stop.prevent="applyPianoMeterEditor()"
                >
              </label>
              <label>
                <span>节拍时长</span>
                <select
                  v-model.number="pianoMeterEditor.denominator"
                  @change="applyPianoMeterEditor()"
                >
                  <option
                    v-for="denominator in timeSignatureDenominatorOptions"
                    :key="denominator"
                    :value="denominator"
                  >
                    {{ denominatorLabel(denominator) }}
                  </option>
                </select>
              </label>
            </div>
            <div
              v-if="pianoHarmonyEditor.open"
              ref="pianoHarmonyEditorRoot"
              class="piano-harmony-popover"
              :style="{ left: `${pianoHarmonyEditor.x}px`, top: `${pianoHarmonyEditor.y}px` }"
              @pointerdown.stop
              @click.stop
            >
              <label>
                <span>和声</span>
                <input
                  v-model="pianoHarmonyEditor.text"
                  type="text"
                  maxlength="64"
                  placeholder="Cmaj7"
                  @blur="applyPianoHarmonyEditor()"
                  @change="applyPianoHarmonyEditor()"
                  @keydown.enter.stop.prevent="applyPianoHarmonyEditor()"
                >
              </label>
            </div>
          </div>
        </div>

        <MixerPanel
          v-if="mixerVisible"
          ref="lowerEditorPanel"
          :context="mixerPanelContext"
        />
      </section>

      <aside
        v-show="inspectorVisible"
        class="inspector"
      >
        <div class="inspector-section">
          <div class="section-title">
            Engine
          </div>
          <dl class="engine-stats">
            <div>
              <dt>Transport</dt>
              <dd>{{ engine?.transport || 'stopped' }}</dd>
            </div>
            <div>
              <dt>Audio WS</dt>
              <dd>{{ audioConnected ? 'connected' : 'disconnected' }}</dd>
            </div>
            <div>
              <dt>Host PCM</dt>
              <dd>{{ hostStreamingEnabled ? 'enabled' : 'disabled' }}</dd>
            </div>
            <div>
              <dt>PCM</dt>
              <dd>{{ pcmStreaming ? 'streaming' : 'idle' }}</dd>
            </div>
            <div>
              <dt>Tracks</dt>
              <dd>{{ tracks.length }}</dd>
            </div>
            <div>
              <dt>Notes</dt>
              <dd>{{ totalNotes }}</dd>
            </div>
          </dl>
        </div>
      </aside>
    </main>

    <TrackCreateDialog
      v-if="trackCreateDialogOpen"
      v-model:name="trackCreateName"
      v-model:color="trackCreateColor"
      v-model:type="trackCreateType"
      v-model:channel-type="trackCreateChannelType"
      v-model:output-bus-id="trackCreateOutputBusId"
      :automation-target-label="automationTargetLabel(trackCreateAutomationTarget)"
      :palette="trackCreatePalette"
      :output-buses="availableOutputBuses(null)"
      @open-automation-parameter-picker="openAutomationParameterPickerForCreate"
      @close="closeTrackCreateDialog"
      @create="createSelectedTrack"
    />

    <StudioExportDialog
      v-if="exportDialogOpen"
      v-model:target="exportTarget"
      v-model:mode="exportMode"
      v-model:format="exportFormat"
      v-model:sample-rate="exportSampleRate"
      v-model:bit-depth="exportBitDepth"
      v-model:bitrate="exportBitrate"
      v-model:selected-track-ids="exportSelectedTrackIds"
      :exportable-tracks="exportableTracks"
      :result="exportResult"
      :error-message="exportErrorMessage"
      :exporting="exporting"
      @close="closeExportDialog"
      @export="exportCurrentAudio"
    />
    <AutomationParameterPickerDialog
      :open="automationParameterPicker.open"
      :default-targets="defaultAutomationTargets"
      :learned-targets="learnedAutomationTargets"
      @close="closeAutomationParameterPicker"
      @bind-target="bindAutomationPickerTarget"
      @refresh-captured="pollCapturedPluginParameters"
      @rename-learned-target="renameLearnedAutomationParameter"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useDawHost } from '@/composables/useDawHost.js'
import ArrangementEditorPanel from './studio/ArrangementEditorPanel.vue'
import AutomationParameterPickerDialog from './studio/AutomationParameterPickerDialog.vue'
import ControllerLanesPanel from './studio/ControllerLanesPanel.vue'
import MixerPanel from './studio/MixerPanel.vue'
import PianoEditorHeader from './studio/PianoEditorHeader.vue'
import StudioExportDialog from './studio/StudioExportDialog.vue'
import StudioTopbar from './studio/StudioTopbar.vue'
import TrackCreateDialog from './studio/TrackCreateDialog.vue'
import { createArrangementRenderer } from './arrangementRenderer.js'
import { createAutomationEditing } from './automationEditing.js'
import { createPianoRollRenderer } from './pianoRollRenderer.js'
import { createRafRedrawScheduler } from './redrawScheduler.js'
import { useStudioKeyboardShortcuts } from './useStudioKeyboardShortcuts.js'
import './studio/StudioDialogs.css'
import {
  CONTROLLER_PRESETS,
  DEFAULT_CONTROLLER_IDS,
  DEFAULT_NOTE_VELOCITY,
  applyCurveAmount,
  controllerDefinitionFromId,
  controllerDisplayRange,
  controllerLaneStackHeight,
  controllerRenderPoints,
  controllerUnitToValue,
  createDefaultControllerLanes,
  eventMatchesController,
  makeControllerEventId,
  makeControllerLaneId,
  normalizeCurveAmount,
  normalizeControllerEvent,
  valueFromControllerEvent,
} from './controllerLanes.js'
import {
  PIANO_QUANTIZE_OPTIONS,
  interpolateControllerValue,
  quantizeStepFromId,
  quantizedBeatsBetween,
  snapBeatToGrid,
} from './pianoQuantize.js'
import { pianoScrollTopForNotes } from './pianoViewport.js'
import {
  buildClipDiffOperations,
  buildMidiEventDiffOperations,
  buildMidiNoteDiffOperations,
} from './studioIncrementalDiff.js'
import {
  beatsToSeconds,
  effectiveTempoAtBeat,
} from './tempoAutomation.js'
import {
  effectiveMeterAtBeat,
  meterPositionAtBeat,
  normalizeMeterEvents,
} from './meterEvents.js'

defineProps({
  embedded: { type: Boolean, default: false },
})

const {
  project,
  projectArchives,
  activeProjectId,
  host,
  engine,
  tracks,
  activeTrack,
  loading,
  exporting,
  hostError,
  audioConnected,
  hostStreamingEnabled,
  pcmStreaming,
  playing,
  positionBeats,
  totalNotes,
  plugins,
  pluginsLoading,
  editorWindows,
  pluginParameters,
  learnedAutomationParameters,
  loadProject,
  loadProjectArchives,
  saveProjectCopy,
  openProjectArchive,
  saveProject,
  diffMidi,
  diffClips,
  transport,
  updateTrack,
  createTrack,
  importAudioFile,
  exportAudio,
  deleteTrack,
  loadPlugins,
  setTrackPlugin,
  openPluginEditor,
  loadPluginParameters,
  setPluginParameter,
  createAutomationTrack,
  retargetAutomationTrack,
  diffAutomationTrack,
  pollCapturedPluginParameters,
  renameLearnedAutomationParameter,
  selectTrack,
  refreshHostStatus,
  connectAudioStream,
  disconnectAudioStream,
} = useDawHost()

const arrangementEditorPanel = ref(null)
const arrangementWrap = computed(() => arrangementEditorPanel.value?.arrangementWrap || null)
const arrangementHeaderCanvas = computed(() => arrangementEditorPanel.value?.arrangementHeaderCanvas || null)
const arrangementCanvas = computed(() => arrangementEditorPanel.value?.arrangementCanvas || null)
const editorStack = ref(null)
const lowerEditorPanel = ref(null)
const pianoWorkspace = ref(null)
const pianoWrap = ref(null)
const pianoHeaderCanvas = ref(null)
const pianoCanvas = ref(null)
const controllerWrap = ref(null)
const timeSignatureRoot = ref(null)
const pianoMeterEditorRoot = ref(null)
const pianoHarmonyEditorRoot = ref(null)
const projectLibraryOpen = ref(false)
const projectCopyTitle = ref('')
const controllerLaneCanvases = new Map()
const automationMenu = ref({ open: false, x: 0, y: 0, target: null, label: '' })
const trackContextMenu = ref({ open: false, x: 0, y: 0, trackId: null, name: '' })
const trackReorderDrag = ref({ trackId: null, overTrackId: null, placement: 'after' })

const defaultPxPerBeat = 56
const supportedAudioImportExtensions = ['aac', 'flac', 'm4a', 'mp3', 'wav']
const supportedAudioImportMimeTypes = new Set([
  'audio/aac',
  'audio/flac',
  'audio/mp3',
  'audio/mp4',
  'audio/mpeg',
  'audio/wav',
  'audio/wave',
  'audio/x-flac',
  'audio/x-m4a',
  'audio/x-wav',
])
const arrangementPxPerBeat = ref(defaultPxPerBeat)
const arrangementScrollLeft = ref(0)
const pianoPxPerBeat = ref(defaultPxPerBeat)
const pianoTimelineWidth = ref(0)
const minArrangementPxPerBeat = 8
const maxArrangementPxPerBeat = 64
const minPianoPxPerBeat = 8
const maxPianoPxPerBeat = 64
const arrangementEmptyBars = 64
const pianoEmptyBars = 32
const defaultTrackListWidth = 246
const minTrackListWidth = 190
const maxTrackListWidth = 420
const trackReorderMidpoint = 0.5
const rulerBeatLabelMinScale = 30
const rulerMajorTickRatio = 1 / 3
const rulerMinorTickRatio = rulerMajorTickRatio / 2
const rulerFineTickRatio = rulerMinorTickRatio / 2
const rulerBarLabelFont = '12px Cascadia Mono, Consolas, monospace'
const rulerBeatLabelFont = '10px Cascadia Mono, Consolas, monospace'
const rulerLabelGap = 2
const trackListWidth = ref(defaultTrackListWidth)
const arrangementRulerH = 30
const arrangementToolbarH = 34
const arrangementTrackH = 72
const pianoKeyW = 76
const pianoRulerH = 24
const pianoMeterLaneH = 28
const pianoSubtrackH = pianoMeterLaneH
const pianoRowH = 12
const controllerLaneTabH = 24
const controllerLaneBodyH = 72
const controllerLaneH = controllerLaneTabH + controllerLaneBodyH
const controllerLaneFooterH = 28
const automationPointHitRadius = 7
const controllerPointHitRadius = 7
const automationCurveHandleHitRadius = 7
const controllerCurveHandleHitRadius = 7
const curveHandleMinSegmentPx = Math.max(automationPointHitRadius, controllerPointHitRadius) * 2 + 4
const curveHandleDragScale = 2
const pianoDrawLongPressMs = 260
const pianoDrawMoveTolerancePx = 5
const pianoMeterEventHitRadius = 9
const pianoHarmonyEventHitRadius = 9
const minPitch = 0
const maxPitch = 120
const trackCreatePalette = ['#4e79ff', '#d95b55', '#5f916b', '#d7b66f', '#b489d6', '#58a7b8']
const pianoSubtrackIds = ['meter', 'harmony']
const timeSignatureDenominatorOptions = [2, 4, 8, 16, 32]
const visualPositionBeats = ref(0)
const timelineTool = ref('select')
const pianoTool = ref('select')
const pianoQuantizeId = ref('1/16')
const pianoSnapEnabled = ref(true)
const timelineQuantizeMenuOpen = ref(false)
const pianoQuantizeMenuOpen = ref(false)
const pianoMeterLaneOpen = ref(false)
const pianoHarmonyLaneOpen = ref(false)
const pianoSubtrackSyncKey = ref('')
const pianoSubtrackOrder = ref([])
const pianoSubtrackCreateValue = ref('')
const tempoInput = ref(120)
const tempoInputFocused = ref(false)
const timeSignatureNumerator = ref(4)
const timeSignatureDenominator = ref(4)
const timeSignatureNumeratorFocused = ref(false)
const timeSignaturePopoverOpen = ref(false)
const timeSignatureDenominatorPopoverOpen = ref(false)
const pianoMeterEditor = ref({
  open: false,
  x: 0,
  y: 0,
  eventIndex: -1,
  beat: 0,
  numerator: 4,
  denominator: 4,
})
const pianoHarmonyEditor = ref({
  open: false,
  x: 0,
  y: 0,
  eventIndex: -1,
  beat: 0,
  text: '',
})
const selectedNoteIds = ref(new Set())
const selectedClipIds = ref(new Set())
const selectedAutomationPoint = ref({ trackId: null, index: -1 })
const selectedControllerEventId = ref(null)
const noteClipboard = ref([])
const clipClipboard = ref([])
const draftNote = ref(null)
const selectionBox = ref(null)
const activeClipId = ref(null)
const lowerEditorMode = ref(null)
const pianoVisible = computed(() => lowerEditorMode.value === 'piano')
const mixerVisible = computed(() => lowerEditorMode.value === 'mixer')
const audioDropActive = ref(false)
const audioImporting = ref(false)
const trackCreateDialogOpen = ref(false)
const trackCreateName = ref('')
const trackCreateColor = ref(trackCreatePalette[0])
const trackCreateType = ref('instrument')
const trackCreateChannelType = ref('multichannel')
const trackCreateOutputBusId = ref(null)
const trackCreateAutomationTarget = ref(null)
const exportDialogOpen = ref(false)
const exportTarget = ref('entire_project')
const exportMode = ref('mixdown')
const exportFormat = ref('wav')
const exportSampleRate = ref(48000)
const exportBitDepth = ref('i24')
const exportBitrate = ref('320k')
const exportSelectedTrackIds = ref([])
const exportResult = ref(null)
const exportErrorMessage = ref('')
const automationParameterPicker = ref({ open: false, mode: 'create', trackId: null })
const lowerEditorPanelHeight = ref(null)
const inspectorVisible = ref(true)
const controllerLanes = ref(createDefaultControllerLanes())
const controllerMenuLaneId = ref(null)
const customControllerNumber = ref('')
const controllerScrollLeft = ref(0)
const controllerPanelHeight = computed(() => controllerLaneStackHeight(
  controllerLanes.value.length,
  controllerLaneH,
  controllerLaneFooterH
))
const pianoQuantizeOptions = PIANO_QUANTIZE_OPTIONS

let resizeObserver = null
let raf = 0
let lastFrame = 0
let drawScheduler = null
let pianoDrag = null
let lowerEditorResizeDrag = null
let trackListResizeDrag = null
let arrangementDrag = null
let controllerDrag = null
let syncingPianoScroll = false
let audioDecodeContext = null
let tempoUpdateTimer = null
let pianoLongPressTimer = null
let learnedParameterPollTimer = null

const snapStep = 0.25
const minFreehandStep = 0.0625
const minArrangementPanelHeight = arrangementToolbarH + arrangementRulerH
const minLowerEditorPanelHeight = 140

const tempo = computed(() => effectiveTempoAtBeat(project.value, visualPositionBeats.value))
const meterBeats = computed(() => {
  const numerator = normalizeTimeSignatureNumerator(project.value?.time_signature?.[0])
  const denominator = normalizeTimeSignatureDenominator(project.value?.time_signature?.[1])
  return numerator * (4 / denominator)
})
const timeSignatureLabel = computed(() => (
  `${timeSignatureNumerator.value} / ${timeSignatureDenominator.value}`
))
const timeSignatureDenominatorLabel = computed(() => (
  denominatorLabel(timeSignatureDenominator.value)
))
const pluginOptions = computed(() => ({
  vst3: Array.isArray(plugins.value?.vst3) ? plugins.value.vst3 : [],
  vst2: Array.isArray(plugins.value?.vst2) ? plugins.value.vst2 : [],
}))
const pianoQuantizeStep = computed(() => quantizeStepFromId(pianoQuantizeId.value))
const pianoQuantizeLabel = computed(() => (
  pianoQuantizeOptions.find(option => option.id === pianoQuantizeId.value)?.label || '1/16'
))
const isPianoSnapActive = computed(() => pianoSnapEnabled.value && pianoQuantizeStep.value !== null)
const activePianoSnapStep = computed(() => (
  isPianoSnapActive.value ? pianoQuantizeStep.value : null
))
const activeNoteStep = computed(() => activePianoSnapStep.value || minFreehandStep)
const hasProjectMeterEvents = computed(() => (
  Array.isArray(project.value?.meter_events) && project.value.meter_events.length > 0
))
const pianoMeterLaneVisible = computed(() => pianoMeterLaneOpen.value)
const pianoHarmonyLaneVisible = computed(() => pianoHarmonyLaneOpen.value)
const pianoVisibleSubtracks = computed(() => (
  pianoSubtrackOrder.value.filter((id) => (
    (id === 'meter' && pianoMeterLaneVisible.value)
    || (id === 'harmony' && pianoHarmonyLaneVisible.value)
  ))
))
const arrangementVisibleSubtracks = computed(() => pianoVisibleSubtracks.value)
const pianoMeterLaneTop = computed(() => pianoSubtrackTop('meter'))
const pianoHarmonyLaneTop = computed(() => pianoSubtrackTop('harmony'))
const pianoNoteTop = computed(() => (
  pianoRulerH + pianoVisibleSubtracks.value.length * pianoSubtrackH
))
const pianoSubtrackOptions = computed(() => [
  { id: 'meter', label: '拍号轨', disabled: pianoMeterLaneVisible.value },
  { id: 'harmony', label: '和声轨', disabled: pianoHarmonyLaneVisible.value },
])
const activeMidiClip = computed(() => {
  for (const track of tracks.value) {
    for (const clip of track.clips || []) {
      if (clip.id === activeClipId.value && clip.type === 'midi') {
        return { track, clip }
      }
    }
  }
  return null
})
const lowerEditorVisible = computed(() => (
  (pianoVisible.value && activeMidiClip.value) || mixerVisible.value
))
const mixerTracks = computed(() => tracks.value.filter(track => !isAutomationTrack(track)))
const exportableTracks = computed(() => tracks.value.filter(track => !isAutomationTrack(track)))
const masterBus = computed(() => normalizeMasterBus(project.value?.master_bus))
const mixerPanelContext = computed(() => ({
  mixerTracks: mixerTracks.value,
  activeTrack: activeTrack.value,
  masterBus: masterBus.value,
  pluginOptions: pluginOptions.value,
  pluginsLoading: pluginsLoading.value,
  startResize: startLowerEditorResize,
  loadPlugins,
  close: closeMixer,
  selectTrack,
  trackTypeLabel,
  canUseMixerInserts,
  mixerInsertSlots,
  pluginSlot,
  uniqueMixerPluginLabel,
  pluginSlotValue,
  pluginSlotLabel,
  pluginSelect: onPluginSelect,
  selectedPluginMissing,
  loadPluginParameters,
  pluginParameterRows,
  openAutomationMenu,
  automationTargetForPluginParameter,
  setLivePluginParameter,
  parameterValueLabel,
  mixerSendRows,
  availableOutputBuses,
  updateTrackSend,
  removeTrackSend,
  addTrackSendChange: onAddTrackSendChange,
  automationTargetForTrackPan,
  updateTrack,
  automationTargetForTrackVolume,
  masterBusPluginSelect: onMasterBusPluginSelect,
  updateMasterBus,
  volumeDbLabel,
}))
const editorStackStyle = computed(() => {
  if (!lowerEditorVisible.value || !lowerEditorPanelHeight.value) return {}
  return {
    gridTemplateRows: `minmax(${minArrangementPanelHeight}px, 1fr) ${lowerEditorPanelHeight.value}px`,
  }
})
const arrangementLayoutStyle = computed(() => ({
  '--track-list-width': `${trackListWidth.value}px`,
}))
const arrangementWrapStyle = computed(() => ({
  '--arrangement-scroll-left': `${arrangementScrollLeft.value}px`,
}))
const arrangementLayoutContext = computed(() => ({
  arrangementStyle: arrangementLayoutStyle.value,
  wrapStyle: arrangementWrapStyle.value,
}))
const arrangementToolbarContext = computed(() => ({
  selectedClipCount: selectedClipIds.value.size,
  timelineQuantizeMenuOpen: timelineQuantizeMenuOpen.value,
  pianoQuantizeLabel: pianoQuantizeLabel.value,
  pianoQuantizeOptions: pianoQuantizeOptions.value,
  pianoQuantizeId: pianoQuantizeId.value,
  pianoSnapActive: isPianoSnapActive.value,
  pianoSubtrackCreateValue: pianoSubtrackCreateValue.value,
  pianoSubtrackOptions: pianoSubtrackOptions.value,
  timelineTool: timelineTool.value,
}))
const arrangementTrackListContext = computed(() => ({
  tracks: tracks.value,
  activeTrack: activeTrack.value,
  visibleSubtracks: arrangementVisibleSubtracks.value,
  pluginOptions: pluginOptions.value,
  canDragTrackRow,
  isTrackReorderDragging,
  isTrackReorderDropTarget,
  trackRowMetaLabel,
  isInstrumentTrack,
  isAudioTrack,
  isBusTrack,
  isAutomationTrack,
  pluginSlot,
  pluginSlotValue,
  pluginSlotLabel,
  selectedPluginMissing,
  availableOutputBuses,
  isPluginEditorOpen,
  canOpenPluginEditor,
  automationTargetLabel,
  automationPointCount,
}))
const arrangementAudioDropContext = computed(() => ({
  active: audioDropActive.value,
  importing: audioImporting.value,
}))
const arrangementContextMenuContext = computed(() => ({
  automation: automationMenu.value,
  track: trackContextMenu.value,
  loading: loading.value,
}))
const positionLabel = computed(() => {
  const position = meterPositionAtBeat(project.value, visualPositionBeats.value)
  return `${position.bar.toString().padStart(5, '0')}.${position.beat.toString().padStart(2, '0')}.${position.ticks.toString().padStart(3, '0')}`
})
const defaultAutomationTargets = computed(() => {
  const targets = [
    {
      key: "global-tempo-bpm",
      label: 'Tempo BPM',
      detail: 'Session tempo',
      target: automationTargetForTempoBpm(),
    },
  ]
  for (const track of tracks.value) {
    if (isAutomationTrack(track)) continue
    targets.push({
      key: `track-volume-${track.id}`,
      label: `${track.name} Volume`,
      detail: 'Track volume',
      target: automationTargetForTrackVolume(track),
    })
    targets.push({
      key: `track-pan-${track.id}`,
      label: `${track.name} Pan`,
      detail: 'Track pan',
      target: automationTargetForTrackPan(track),
    })
    for (const slot of track.plugin_slots || []) {
      if (!slot || slot.type !== 'vst3') continue
      for (const param of pluginParameterRows(track.id, slot.id).filter(param => param.automatable !== false)) {
        targets.push({
          key: `plugin-${track.id}-${slot.id}-${param.index}`,
          label: `${track.name} / ${slot.name} / ${param.name || `Parameter ${param.index}`}`,
          detail: param.units || 'VST parameter',
          target: automationTargetForPluginParameter(track, slot.id, param),
        })
      }
    }
  }
  return targets
})
const learnedAutomationTargets = computed(() => (
  learnedAutomationParameters.value.map(item => ({
    id: item.id,
    name: item.name,
    detail: learnedAutomationTargetDetail(item),
    target: {
      ...(item.target || {}),
      label: item.name,
    },
  }))
))

const {
  automationCurveHandlePoint,
  automationCurveValueAtBeat,
  automationPointY,
  cancelAutomationDrag,
  hitTestAutomationCurveHandle,
  hitTestAutomationPoint,
  sortAutomationPoints,
  startAutomationCurveDrag,
  startAutomationDrag,
  startAutomationPointDrag,
} = createAutomationEditing({
  activePianoSnapStep,
  arrangementPoint,
  arrangementPxPerBeat,
  arrangementTrackH,
  arrangementTrackTop,
  automationCurveHandleHitRadius,
  automationPointHitRadius,
  curveHandleDragScale,
  curveHandleMinSegmentPx,
  diffAutomationTrack,
  drawAll,
  onAutomationPersistError: (error) => {
    if (!hostError.value) hostError.value = error?.message || 'Failed to apply automation edit'
  },
  project,
  selectedAutomationPoint,
  tracks,
})

const {
  drawArrangement,
} = createArrangementRenderer({
  activeClipId,
  activePianoSnapStep,
  activeTrack,
  arrangementCanvas,
  arrangementEmptyBars,
  arrangementHeaderCanvas,
  arrangementPxPerBeat,
  arrangementRulerH,
  arrangementSubtrackTop,
  arrangementTrackH,
  arrangementTrackTop,
  arrangementVisibleSubtracks,
  arrangementWrap,
  automationCurveHandlePoint,
  automationCurveValueAtBeat,
  automationPointY,
  automationTargetLabel,
  clipRect,
  currentTrackListWidth,
  editableHarmonyEvents,
  isAutomationTrack,
  meterBeats,
  pianoSubtrackH,
  project,
  rulerBarLabelFont,
  rulerBeatLabelFont,
  rulerBeatLabelMinScale,
  rulerFineTickRatio,
  rulerLabelGap,
  rulerMajorTickRatio,
  rulerMinorTickRatio,
  selectedAutomationPoint,
  selectedClipIds,
  snapStep,
  sortAutomationPoints,
  tracks,
  visualPositionBeats,
})

const {
  controllerCurveHandlePoint,
  controllerValueToY,
  drawControllerLanes,
  drawPiano,
} = createPianoRollRenderer({
  activeMidiClip,
  activePianoSnapStep,
  controllerLaneBodyH,
  controllerLaneCanvases,
  controllerLaneH,
  controllerLaneTabH,
  controllerLanes,
  controllerScrollLeft,
  controllerWrap,
  controllerDefinitionForLane,
  curveHandleMinSegmentPx,
  draftNote,
  editableHarmonyEvents,
  maxPitch,
  meterBeats,
  minPitch,
  noteRect,
  pianoCanvas,
  pianoEmptyBars,
  pianoHarmonyLaneTop,
  pianoHarmonyLaneVisible,
  pianoHeaderCanvas,
  pianoKeyW,
  pianoMeterLaneH,
  pianoMeterLaneTop,
  pianoMeterLaneVisible,
  pianoNoteTop,
  pianoPxPerBeat,
  pianoRowH,
  pianoRulerH,
  pianoSubtrackH,
  pianoTimelineWidth,
  pianoVisible,
  pianoVisibleSubtracks,
  pianoWrap,
  project,
  rulerBarLabelFont,
  rulerBeatLabelFont,
  rulerBeatLabelMinScale,
  rulerFineTickRatio,
  rulerLabelGap,
  rulerMajorTickRatio,
  rulerMinorTickRatio,
  selectedControllerEventId,
  selectedNoteIds,
  selectionBox,
  snapStep,
  visualPositionBeats,
})

const {
  onStudioKeydown,
  onTrackRowKeydown,
} = useStudioKeyboardShortcuts({
  activeMidiClip,
  automationMenu,
  cancelAutomationDrag,
  clearPianoLongPressTimer,
  closePianoHarmonyEditor,
  closePianoMeterEditor,
  closeTrackContextMenu,
  controllerMenuLaneId,
  copySelectedClips,
  copySelectedNotes,
  deleteSelectedClips,
  deleteSelectedNotes,
  draftNote,
  drawAll,
  noteClipboard,
  pasteClips,
  pasteNotes,
  pianoQuantizeMenuOpen,
  pianoVisible,
  selectTrack,
  selectedAutomationPoint,
  selectedClipIds,
  selectedControllerEventId,
  selectedNoteIds,
  selectionBox,
  timelineQuantizeMenuOpen,
  togglePlay,
  tracks,
})

async function toggleProjectLibrary() {
  projectLibraryOpen.value = !projectLibraryOpen.value
  if (projectLibraryOpen.value) {
    await loadProjectArchives()
  }
}

function archiveTimeLabel(archive) {
  const value = archive?.saved_at || archive?.updated_at || ''
  if (!value) return 'Unsaved'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function saveCurrentProjectCopy() {
  const title = projectCopyTitle.value.trim()
  const fallback = `${project.value?.title || 'ATRI Session'} Copy`
  const res = await saveProjectCopy(title || fallback)
  if (!res) return
  projectCopyTitle.value = ''
  projectLibraryOpen.value = false
  syncTransportDisplayFields(project.value)
  drawAll()
}

async function openArchivedProject(projectId) {
  if (!projectId || projectId === activeProjectId.value) {
    projectLibraryOpen.value = false
    return
  }
  const res = await openProjectArchive(projectId)
  if (!res) return
  projectLibraryOpen.value = false
  syncTransportDisplayFields(project.value)
  drawAll()
}

function cloneProject() {
  return JSON.parse(JSON.stringify(project.value || {}))
}

function normalizeMasterBus(bus = {}) {
  const source = bus && typeof bus === 'object' ? bus : {}
  const volume = Number(source.volume ?? 1)
  const pan = Number(source.pan ?? 0)
  return {
    id: 'master',
    type: 'bus',
    name: source.name || 'Master Bus',
    color: source.color || '#58a7b8',
    volume: Number.isFinite(volume) ? clamp(volume, 0, 2) : 1,
    pan: Number.isFinite(pan) ? clamp(pan, -1, 1) : 0,
    mute: Boolean(source.mute),
    solo: Boolean(source.solo),
    plugin_slots: Array.isArray(source.plugin_slots) ? source.plugin_slots : [],
  }
}

function findProjectTrack(nextProject, trackId) {
  return (nextProject.tracks || []).find(track => track.id === trackId)
}

function findClipRecord(clipId) {
  for (const track of tracks.value) {
    const clip = (track.clips || []).find(item => item.id === clipId)
    if (clip) return { track, clip }
  }
  return null
}

async function persistProjectUpdate(updater) {
  if (!project.value) return null
  const nextProject = cloneProject()
  updater(nextProject)
  const res = await saveProject(nextProject, { broadcast: true })
  drawAll()
  return res
}

function normalizeTempo(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 120
  return Math.round(Math.max(1, parsed) * 10) / 10
}

function syncTempoField(nextProject) {
  if (tempoInputFocused.value) return
  tempoInput.value = normalizeTempo(effectiveTempoAtBeat(nextProject, visualPositionBeats.value))
}

async function updateTempo() {
  if (!project.value) return
  const nextTempo = normalizeTempo(tempoInput.value)
  tempoInput.value = nextTempo
  await persistProjectUpdate((nextProject) => {
    nextProject.tempo = nextTempo
  })
}

function scheduleTempoUpdate() {
  clearTimeout(tempoUpdateTimer)
  tempoUpdateTimer = setTimeout(() => {
    updateTempo()
  }, 160)
}

function onTempoWheel(event) {
  const direction = event.deltaY < 0 ? 1 : -1
  const step = event.shiftKey ? 0.1 : 1
  tempoInput.value = normalizeTempo(Number(tempoInput.value || tempo.value) + direction * step)
  scheduleTempoUpdate()
}

function normalizeTimeSignatureNumerator(value) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed)) return 4
  return clamp(parsed, 1, 255)
}

function normalizeTimeSignatureDenominator(value) {
  const parsed = Number.parseInt(value, 10)
  return timeSignatureDenominatorOptions.includes(parsed) ? parsed : 4
}

function denominatorLabel(denominator) {
  return `1/${normalizeTimeSignatureDenominator(denominator)}`
}

function syncTimeSignatureFields(nextProject) {
  const meter = effectiveMeterAtBeat(nextProject, visualPositionBeats.value)
  if (!timeSignatureNumeratorFocused.value) {
    timeSignatureNumerator.value = normalizeTimeSignatureNumerator(meter.numerator)
  }
  timeSignatureDenominator.value = normalizeTimeSignatureDenominator(meter.denominator)
}

function syncTransportDisplayFields(nextProject = project.value) {
  syncTempoField(nextProject)
  syncTimeSignatureFields(nextProject)
}

// Syncs lane visibility only when persisted subtrack data changes, preserving manual collapse across unrelated saves.
function syncPianoSubtrackLanes(nextProject) {
  if (!nextProject) return
  const syncKey = pianoSubtrackProjectSyncKey(nextProject)
  if (pianoSubtrackSyncKey.value === syncKey) return
  const order = normalizePianoSubtrackOrder(nextProject.piano_subtrack_order)
  if (Array.isArray(nextProject.meter_events) && nextProject.meter_events.length > 0 && !order.includes('meter')) {
    order.push('meter')
  }
  if (Array.isArray(nextProject.harmony_events) && nextProject.harmony_events.length > 0 && !order.includes('harmony')) {
    order.push('harmony')
  }
  pianoSubtrackOrder.value = order
  pianoMeterLaneOpen.value = order.includes('meter')
  pianoHarmonyLaneOpen.value = order.includes('harmony')
  pianoSubtrackSyncKey.value = syncKey
}

function pianoSubtrackProjectSyncKey(nextProject) {
  return JSON.stringify({
    order: normalizePianoSubtrackOrder(nextProject?.piano_subtrack_order),
    meterEvents: normalizeEditableMeterEvents(nextProject?.meter_events || []),
    harmonyEvents: normalizeEditableHarmonyEvents(nextProject?.harmony_events || []),
  })
}

function normalizePianoSubtrackOrder(order) {
  const nextOrder = []
  for (const id of Array.isArray(order) ? order : []) {
    const subtrackId = String(id || '').trim().toLowerCase()
    if (!pianoSubtrackIds.includes(subtrackId) || nextOrder.includes(subtrackId)) continue
    nextOrder.push(subtrackId)
  }
  return nextOrder
}

function pianoSubtrackOrderWith(subtrackId) {
  return normalizePianoSubtrackOrder([...pianoSubtrackOrder.value, subtrackId])
}

function pianoSubtrackTop(subtrackId) {
  const index = pianoVisibleSubtracks.value.indexOf(subtrackId)
  return pianoRulerH + Math.max(0, index) * pianoSubtrackH
}

function arrangementSubtrackTop(subtrackId) {
  const index = arrangementVisibleSubtracks.value.indexOf(subtrackId)
  return arrangementRulerH + Math.max(0, index) * pianoSubtrackH
}

function arrangementTrackTop(trackIndex) {
  return arrangementRulerH + arrangementVisibleSubtracks.value.length * pianoSubtrackH
    + Math.max(0, trackIndex) * arrangementTrackH
}

function arrangementTrackIndexAtY(y) {
  return Math.floor((Number(y || 0) - arrangementTrackTop(0)) / arrangementTrackH)
}

function toggleTimeSignaturePopover() {
  timeSignaturePopoverOpen.value = !timeSignaturePopoverOpen.value
  if (!timeSignaturePopoverOpen.value) {
    timeSignatureDenominatorPopoverOpen.value = false
  }
}

function closeTimeSignaturePopover() {
  timeSignaturePopoverOpen.value = false
  timeSignatureDenominatorPopoverOpen.value = false
}

function closeTrackContextMenu() {
  trackContextMenu.value = { open: false, x: 0, y: 0, trackId: null, name: '' }
}

// Track reordering persists the shared project order so the sidebar, canvas, and rack stay aligned.
function canDragTrackRow(track) {
  return track?.id != null && tracks.value.length > 1 && !loading.value
}

function isTrackReorderInteractiveTarget(target) {
  return Boolean(target?.closest?.('button, input, select, textarea, a'))
}

function sameTrackId(left, right) {
  return left != null && right != null && String(left) === String(right)
}

function trackOrderKey(trackList) {
  return (trackList || []).map(track => String(track.id)).join('|')
}

function moveTrackInList(trackList, sourceTrackId, targetTrackId, placement) {
  const nextTracks = [...(trackList || [])]
  const sourceIndex = nextTracks.findIndex(track => sameTrackId(track.id, sourceTrackId))
  const targetIndex = nextTracks.findIndex(track => sameTrackId(track.id, targetTrackId))
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return nextTracks

  const [movedTrack] = nextTracks.splice(sourceIndex, 1)
  const targetIndexAfterRemoval = nextTracks.findIndex(track => sameTrackId(track.id, targetTrackId))
  if (targetIndexAfterRemoval < 0) return trackList || []

  const insertIndex = placement === 'before'
    ? targetIndexAfterRemoval
    : targetIndexAfterRemoval + 1
  nextTracks.splice(insertIndex, 0, movedTrack)
  return nextTracks
}

function trackReorderPlacementFromEvent(event) {
  const rect = event.currentTarget?.getBoundingClientRect?.()
  if (!rect?.height) return 'after'
  const localY = Number(event.clientY || 0) - rect.top
  return localY < rect.height * trackReorderMidpoint ? 'before' : 'after'
}

function draggedTrackIdFromEvent(event) {
  const rawTrackId = trackReorderDrag.value.trackId ?? event.dataTransfer?.getData('text/plain')
  const track = tracks.value.find(item => sameTrackId(item.id, rawTrackId))
  return track?.id ?? rawTrackId
}

function isTrackReorderDragging(track) {
  return sameTrackId(trackReorderDrag.value.trackId, track?.id)
}

function isTrackReorderDropTarget(track, placement) {
  const drag = trackReorderDrag.value
  return Boolean(
    drag.trackId != null
    && !sameTrackId(drag.trackId, track?.id)
    && sameTrackId(drag.overTrackId, track?.id)
    && drag.placement === placement
  )
}

function startTrackReorderDrag(event, track) {
  if (!canDragTrackRow(track) || isTrackReorderInteractiveTarget(event.target)) {
    event.preventDefault()
    return
  }
  closeTrackContextMenu()
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  selectTrack(track.id)
  trackReorderDrag.value = { trackId: track.id, overTrackId: track.id, placement: 'after' }
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(track.id))
  }
}

function onTrackReorderDragOver(event, track) {
  if (trackReorderDrag.value.trackId == null || !canDragTrackRow(track)) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  trackReorderDrag.value = {
    ...trackReorderDrag.value,
    overTrackId: track.id,
    placement: trackReorderPlacementFromEvent(event),
  }
}

function endTrackReorderDrag() {
  trackReorderDrag.value = { trackId: null, overTrackId: null, placement: 'after' }
}

async function dropTrackReorder(event, targetTrack) {
  const sourceTrackId = draggedTrackIdFromEvent(event)
  const placement = trackReorderPlacementFromEvent(event)
  endTrackReorderDrag()
  if (
    sourceTrackId == null
    || targetTrack?.id == null
    || sameTrackId(sourceTrackId, targetTrack.id)
    || loading.value
  ) return

  const reorderedTracks = moveTrackInList(tracks.value, sourceTrackId, targetTrack.id, placement)
  if (trackOrderKey(reorderedTracks) === trackOrderKey(tracks.value)) return

  await persistProjectUpdate((nextProject) => {
    nextProject.tracks = moveTrackInList(nextProject.tracks || [], sourceTrackId, targetTrack.id, placement)
  })
  selectTrack(sourceTrackId)
}

function onDocumentPointerDown(event) {
  if (automationMenu.value.open) {
    automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  }
  if (trackContextMenu.value.open) {
    closeTrackContextMenu()
  }
  const meterRoot = pianoMeterEditorRoot.value
  if (pianoMeterEditor.value.open && (!meterRoot || !meterRoot.contains(event.target))) {
    closePianoMeterEditor()
  }
  const harmonyRoot = pianoHarmonyEditorRoot.value
  if (pianoHarmonyEditor.value.open && (!harmonyRoot || !harmonyRoot.contains(event.target))) {
    applyPianoHarmonyEditor().finally(() => closePianoHarmonyEditor()).catch(() => null)
  }
  const root = timeSignatureRoot.value
  if (!root || root.contains(event.target)) return
  closeTimeSignaturePopover()
}

async function updateTimeSignature() {
  if (!project.value) return
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  timeSignatureNumerator.value = numerator
  timeSignatureDenominator.value = denominator
  await applyTransportTimeSignatureChange()
}

async function setTimeSignatureDenominator(denominator) {
  timeSignatureDenominator.value = normalizeTimeSignatureDenominator(denominator)
  timeSignatureDenominatorPopoverOpen.value = false
  await updateTimeSignature()
}

function isAtTimelineStart(beat) {
  return Math.abs(Number(beat || 0)) < 0.0001
}

async function applyTransportTimeSignatureChange() {
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  const changeBeat = Math.max(0, roundPianoBeat(visualPositionBeats.value))
  await persistProjectUpdate((nextProject) => {
    if (isAtTimelineStart(changeBeat)) {
      nextProject.time_signature = [numerator, denominator]
      nextProject.meter_events = normalizeEditableMeterEvents(nextProject.meter_events || [])
        .map(event => (
          isAtTimelineStart(event.beat)
            ? { ...event, numerator, denominator }
            : event
        ))
      return
    }
    ensureBaseMeterEventIfNeeded(nextProject, changeBeat)
    upsertMeterEventInProject(nextProject, changeBeat, numerator, denominator)
  })
}

async function createPianoSubtrack() {
  const value = pianoSubtrackCreateValue.value
  pianoSubtrackCreateValue.value = ''
  if (value === 'meter') {
    await togglePianoMeterLane()
  } else if (value === 'harmony') {
    await togglePianoHarmonyLane()
  }
}

// Toggles the piano meter lane without deleting existing meter events.
async function togglePianoMeterLane() {
  if (!project.value) return
  if (pianoMeterLaneVisible.value) {
    pianoMeterLaneOpen.value = false
    closePianoMeterEditor()
    drawAll()
    return
  }
  await createPianoMeterLane()
  pianoMeterLaneOpen.value = true
  drawAll()
}

// Toggles the harmony marker lane without deleting saved harmony labels.
async function togglePianoHarmonyLane() {
  if (!project.value) return
  if (pianoHarmonyLaneVisible.value) {
    pianoHarmonyLaneOpen.value = false
    closePianoHarmonyEditor()
    drawAll()
    return
  }
  await createPianoHarmonyLane()
  pianoHarmonyLaneOpen.value = true
  drawAll()
}

// Creates the backing meter event list only when the project does not have one yet.
async function createPianoMeterLane() {
  if (!project.value) return
  const nextOrder = pianoSubtrackOrderWith('meter')
  if (hasProjectMeterEvents.value && pianoSubtrackOrder.value.includes('meter')) return
  await persistProjectUpdate((nextProject) => {
    if (!hasProjectMeterEvents.value) {
      nextProject.meter_events = normalizeMeterEvents(nextProject)
    }
    nextProject.piano_subtrack_order = normalizePianoSubtrackOrder(nextOrder)
  })
  pianoSubtrackOrder.value = nextOrder
}

async function createPianoHarmonyLane() {
  if (!project.value) return
  const nextOrder = pianoSubtrackOrderWith('harmony')
  if (pianoSubtrackOrder.value.includes('harmony')) return
  await persistProjectUpdate((nextProject) => {
    nextProject.harmony_events = normalizeEditableHarmonyEvents(nextProject.harmony_events || [])
    nextProject.piano_subtrack_order = normalizePianoSubtrackOrder(nextOrder)
  })
  pianoSubtrackOrder.value = nextOrder
}

async function upsertMeterEventAtBeat(beat) {
  if (!project.value) return
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  const nextBeat = Math.max(0, snapBeatToGrid(beat, activePianoSnapStep.value))
  await persistProjectUpdate((nextProject) => {
    upsertMeterEventInProject(nextProject, nextBeat, numerator, denominator)
  })
}

async function persistMeterEvents(events) {
  if (!project.value) return
  const normalized = normalizeEditableMeterEvents(events)
  await persistProjectUpdate((nextProject) => {
    nextProject.meter_events = normalized
  })
}

async function deleteMeterEventAtIndex(index) {
  const events = editableMeterEvents()
  if (!events[index]) return
  events.splice(index, 1)
  await persistMeterEvents(events)
}

function baseMeterEvent(nextProject) {
  const meter = Array.isArray(nextProject?.time_signature) ? nextProject.time_signature : [4, 4]
  return {
    beat: 0,
    numerator: normalizeTimeSignatureNumerator(meter[0]),
    denominator: normalizeTimeSignatureDenominator(meter[1]),
  }
}

function ensureBaseMeterEventIfNeeded(nextProject, changeBeat) {
  if (isAtTimelineStart(changeBeat)) return
  const events = normalizeEditableMeterEvents(nextProject.meter_events || [])
  const hasPreviousMarker = events.some(event => Number(event.beat || 0) < changeBeat - 0.0001)
  if (!hasPreviousMarker) {
    events.push(baseMeterEvent(nextProject))
  }
  nextProject.meter_events = normalizeEditableMeterEvents(events)
}

function upsertMeterEventInProject(nextProject, beat, numerator, denominator) {
  nextProject.meter_events = normalizeEditableMeterEvents([
    ...(Array.isArray(nextProject.meter_events) ? nextProject.meter_events : []),
    { beat, numerator, denominator },
  ])
}

function defaultTrackNameForType(type) {
  if (type === 'audio') return 'Audio Track'
  if (type === 'bus') return 'Bus'
  return 'Instrument'
}

function defaultTrackCreateColor() {
  return trackCreatePalette[tracks.value.length % trackCreatePalette.length]
}

function openTrackCreateDialog() {
  trackCreateName.value = ''
  trackCreateColor.value = defaultTrackCreateColor()
  trackCreateType.value = 'instrument'
  trackCreateChannelType.value = 'multichannel'
  trackCreateOutputBusId.value = null
  trackCreateAutomationTarget.value = automationUnassignedTarget()
  trackCreateDialogOpen.value = true
}

function closeTrackCreateDialog() {
  trackCreateDialogOpen.value = false
}

function openTrackContextMenu(event, track) {
  if (!track) return
  event.preventDefault()
  event.stopPropagation()
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  selectTrack(track.id)
  const menuWidth = 190
  trackContextMenu.value = {
    open: true,
    x: Math.max(0, Math.min(Number(event.clientX || 0), window.innerWidth - menuWidth)),
    y: Math.max(0, Math.min(Number(event.clientY || 0), window.innerHeight - 80)),
    trackId: track.id,
    name: track.name || `Track ${track.id}`,
  }
}

async function deleteTrackFromContextMenu() {
  const trackId = trackContextMenu.value.trackId
  closeTrackContextMenu()
  if (!trackId || tracks.value.length <= 1 || loading.value) return
  await deleteTrack(trackId)
}

async function createSelectedTrack() {
  if (trackCreateType.value === 'automation') {
    const target = trackCreateAutomationTarget.value || automationUnassignedTarget()
    const name = trackCreateName.value.trim() || target.label || 'Automation'
    const res = await createAutomationTrackForTarget(target, {
      name,
      color: trackCreateColor.value,
    })
    closeTrackCreateDialog()
    return res
  }
  const type = trackCreateType.value === 'audio'
    ? 'audio'
    : trackCreateType.value === 'bus'
      ? 'bus'
      : 'instrument'
  const channelType = trackCreateChannelType.value === 'mono' ? 'mono' : 'multichannel'
  const name = trackCreateName.value.trim() || defaultTrackNameForType(type)
  const res = await createTrack(name, {
    type,
    color: trackCreateColor.value,
    channel_type: type === 'audio' ? channelType : 'multichannel',
    output_bus_id: trackCreateOutputBusId.value,
  })
  closeTrackCreateDialog()
  return res
}

function defaultExportTrackIds() {
  const activeId = activeTrack.value && !isAutomationTrack(activeTrack.value)
    ? activeTrack.value.id
    : null
  if (activeId != null) return [activeId]
  return exportableTracks.value[0]?.id != null ? [exportableTracks.value[0].id] : []
}

function openExportDialog() {
  exportDialogOpen.value = true
  exportResult.value = null
  exportErrorMessage.value = ''
  exportTarget.value = 'entire_project'
  exportMode.value = 'mixdown'
  exportFormat.value = 'wav'
  exportSampleRate.value = Number(host.value?.sample_rate || 48000)
  if (![44100, 48000, 96000, 192000].includes(exportSampleRate.value)) {
    exportSampleRate.value = 48000
  }
  exportBitDepth.value = 'i24'
  exportBitrate.value = '320k'
  exportSelectedTrackIds.value = defaultExportTrackIds()
}

function closeExportDialog() {
  if (exporting.value) return
  exportDialogOpen.value = false
}

function exportPayloadTrackIds() {
  const seen = new Set()
  return exportSelectedTrackIds.value
    .map(id => Number(id))
    .filter((id) => {
      if (!Number.isFinite(id) || seen.has(id)) return false
      seen.add(id)
      return true
    })
}

function triggerExportDownload(exportItem) {
  if (!exportItem?.download_url || typeof document === 'undefined') return
  const link = document.createElement('a')
  link.href = exportItem.download_url
  link.download = exportItem.filename || ''
  link.rel = 'noreferrer'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function exportCurrentAudio() {
  exportErrorMessage.value = ''
  exportResult.value = null
  const selectedTrackIds = exportPayloadTrackIds()
  if (exportTarget.value === 'selected_tracks' && !selectedTrackIds.length) {
    exportErrorMessage.value = 'Select at least one track'
    return null
  }
  try {
    const res = await exportAudio({
      target: exportTarget.value,
      track_ids: exportTarget.value === 'selected_tracks' ? selectedTrackIds : [],
      mode: exportMode.value,
      format: exportFormat.value,
      sample_rate: exportSampleRate.value,
      bit_depth: exportFormat.value === 'mp3' ? 'i24' : exportBitDepth.value,
      bitrate: exportFormat.value === 'mp3' ? exportBitrate.value : null,
    })
    exportResult.value = res.export || null
    triggerExportDownload(exportResult.value)
    return res
  } catch (err) {
    exportErrorMessage.value = err.message || 'Export failed'
    return null
  }
}

function automationUnassignedTarget() {
  return { kind: 'unassigned', label: 'Unassigned' }
}

function openAutomationParameterPickerForCreate() {
  automationParameterPicker.value = { open: true, mode: 'create', trackId: null }
  loadAutomationPickerPluginParameters()
  pollCapturedPluginParameters().catch(() => null)
}

function openAutomationParameterPickerForTrack(track) {
  automationParameterPicker.value = { open: true, mode: 'track', trackId: track.id }
  loadAutomationPickerPluginParameters()
  pollCapturedPluginParameters().catch(() => null)
}

function closeAutomationParameterPicker() {
  automationParameterPicker.value = { open: false, mode: 'create', trackId: null }
}

async function bindAutomationPickerTarget(target) {
  if (!target) return
  if (automationParameterPicker.value.mode === 'create') {
    trackCreateAutomationTarget.value = target
    closeAutomationParameterPicker()
    return
  }
  const trackId = automationParameterPicker.value.trackId
  if (!trackId) return
  await retargetAutomationTrack(trackId, target)
  closeAutomationParameterPicker()
}

function loadAutomationPickerPluginParameters() {
  for (const track of tracks.value) {
    if (!isInstrumentTrack(track)) continue
    for (const slot of track.plugin_slots || []) {
      if (slot?.type === 'vst3') {
        loadPluginParameters(track.id, slot.id).catch(() => null)
      }
    }
  }
}

function makeClip(type = 'midi', start = 0, color = activeTrack.value?.color) {
  const duration = 4
  return {
    id: makeClipId(),
    type,
    name: type === 'midi' ? 'MIDI Clip' : 'Audio Clip',
    start: Math.max(0, roundPianoBeat(start)),
    duration,
    color: color || '#4e79ff',
    source: '',
    path: '',
    notes: [],
    events: [],
  }
}

async function createMidiClipAtBeat(trackId, beat) {
  const sourceTrack = tracks.value.find(track => Number(track.id) === Number(trackId))
  if (!sourceTrack || !isInstrumentTrack(sourceTrack)) return null
  const clip = makeClip('midi', snapBeatToGrid(beat, activePianoSnapStep.value), sourceTrack.color)
  await persistProjectUpdate((nextProject) => {
    const track = findProjectTrack(nextProject, trackId)
    if (!track) return
    track.clips = [...(track.clips || []), clip]
  })
  selectTrack(trackId)
  selectedClipIds.value = new Set([clip.id])
  activeClipId.value = clip.id
  openPiano()
  drawAll()
  return clip
}

async function drawTimelineMidiAtPoint(point) {
  const track = tracks.value[point?.trackIndex]
  if (!track || !isInstrumentTrack(track)) return null
  return await createMidiClipAtBeat(track.id, point.beat)
}

function isAudioFile(file) {
  if (!file) return false
  const mimeType = String(file.type || '').toLowerCase()
  if (supportedAudioImportMimeTypes.has(mimeType)) return true
  const name = String(file.name || '').toLowerCase()
  return supportedAudioImportExtensions.some(extension => name.endsWith(`.${extension}`))
}

function hasAudioDrag(event) {
  const items = Array.from(event.dataTransfer?.items || [])
  if (
    items.some(item => (
      item.kind === 'file'
      && supportedAudioImportMimeTypes.has(String(item.type || '').toLowerCase())
    ))
  ) {
    return true
  }
  const files = Array.from(event.dataTransfer?.files || [])
  return files.some(isAudioFile)
}

function onAudioDragEnter(event) {
  if (!hasAudioDrag(event)) return
  audioDropActive.value = true
}

function onAudioDragOver(event) {
  if (!hasAudioDrag(event)) return
  event.dataTransfer.dropEffect = 'copy'
  audioDropActive.value = true
}

function onAudioDragLeave(event) {
  if (!audioDropActive.value) return
  const wrap = arrangementWrap.value
  if (wrap?.contains(event.relatedTarget)) return
  audioDropActive.value = false
}

async function onAudioDrop(event) {
  audioDropActive.value = false
  const files = Array.from(event.dataTransfer?.files || []).filter(isAudioFile)
  if (!files.length) return

  const start = snapBeat(arrangementDropBeat(event))
  audioImporting.value = true
  try {
    for (const file of files) {
      const prepared = await prepareAudioImport(file)
      const res = await importAudioFile(prepared.file, {
        start,
        duration_seconds: prepared.durationSeconds,
        waveform: prepared.waveform,
        original_name: file.name || prepared.file.name,
      })
      if (res?.clip?.id) {
        selectedClipIds.value = new Set([res.clip.id])
        activeClipId.value = res.clip.id
        closePiano()
      }
    }
  } catch (err) {
    hostError.value = err.message || 'Failed to import audio'
  } finally {
    audioImporting.value = false
    drawAll()
  }
}

function arrangementDropBeat(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return visualPositionBeats.value
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  if (x < 0) return visualPositionBeats.value
  return Math.max(0, x / arrangementPxPerBeat.value)
}

async function prepareAudioImport(file) {
  let durationSeconds = null
  let waveform = []
  try {
    const buffer = await decodeAudioFile(file)
    durationSeconds = buffer.duration
    waveform = waveformPeaks(buffer, 384)
  } catch {}
  return {
    file,
    durationSeconds,
    waveform,
  }
}

async function decodeAudioFile(file) {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext
  if (!AudioContextCtor) {
    throw new Error('Audio decoding is not supported by this browser')
  }
  if (!audioDecodeContext) {
    audioDecodeContext = new AudioContextCtor()
  }
  const data = await file.arrayBuffer()
  return audioDecodeContext.decodeAudioData(data.slice(0))
}

function waveformPeaks(buffer, buckets = 384) {
  const channels = Math.max(1, Math.min(2, buffer.numberOfChannels || 1))
  const channelData = Array.from({ length: channels }, (_, index) => buffer.getChannelData(index))
  const bucketCount = Math.max(32, Math.min(512, buckets))
  const peaks = []
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = Math.floor((bucket / bucketCount) * buffer.length)
    const end = Math.max(start + 1, Math.floor(((bucket + 1) / bucketCount) * buffer.length))
    let min = 1
    let max = -1
    let peak = 0
    let sumSquares = 0
    let sampleCount = 0
    for (let index = start; index < end; index += 1) {
      let mixed = 0
      for (let channel = 0; channel < channels; channel += 1) {
        const value = channelData[channel][index] || 0
        mixed += value
        peak = Math.max(peak, Math.abs(value))
      }
      mixed /= channels
      min = Math.min(min, mixed)
      max = Math.max(max, mixed)
      peak = Math.max(peak, Math.abs(mixed))
      sumSquares += mixed * mixed
      sampleCount += 1
    }
    if (!sampleCount) {
      for (let index = start; index < end; index += 1) {
        const value = channelData[0][index] || 0
        min = Math.min(min, value)
        max = Math.max(max, value)
        peak = Math.max(peak, Math.abs(value))
        sumSquares += value * value
        sampleCount += 1
      }
    }
    const rms = sampleCount ? Math.sqrt(sumSquares / sampleCount) : 0
    peaks.push({
      min: clamp(Number.isFinite(min) ? min : 0, -1, 1),
      max: clamp(Number.isFinite(max) ? max : 0, -1, 1),
      rms: clamp(rms, 0, 1),
      peak: clamp(peak, 0, 1),
    })
  }
  return peaks
}

function openFirstMidiClip() {
  const preferredTracks = activeTrack.value
    ? [activeTrack.value, ...tracks.value.filter(track => track.id !== activeTrack.value.id)]
    : tracks.value
  for (const track of preferredTracks) {
    const clip = (track.clips || []).find(item => item.type === 'midi')
    if (!clip) continue
    selectTrack(track.id)
    activeClipId.value = clip.id
    selectedClipIds.value = new Set([clip.id])
    openPiano()
    return
  }
}

async function togglePlay() {
  await transport(playing.value ? 'pause' : 'play')
}

async function stopPlayback() {
  await transport('stop')
}

async function seekToBeat(beat) {
  const nextBeat = Math.max(0, Number(beat || 0))
  const previousBeat = visualPositionBeats.value
  visualPositionBeats.value = nextBeat
  try {
    await transport('seek', { position: beatsToSeconds(project.value, nextBeat) })
  } catch {
    visualPositionBeats.value = previousBeat
  }
  drawAll()
}

async function onArrangementPointerDown(event) {
  const canvas = arrangementCanvasForEvent(event)
  if (!canvas || !project.value) return
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  closeTrackContextMenu()
  event.preventDefault()
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  let y = event.clientY - rect.top
  if (canvas === arrangementCanvas.value) y += arrangementTrackTop(0)
  const beat = Math.max(0, x / arrangementPxPerBeat.value)
  const point = arrangementPoint(event)
  if (y <= arrangementRulerH) {
    arrangementDrag = {
      type: 'pan',
      canvas: 'header',
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: arrangementWrap.value?.scrollLeft || 0,
      startBeat: beat,
      moved: false,
    }
    bindArrangementDrag()
    return
  }

  const hit = hitTestArrangementClip(x, y)
  if (hit) {
    selectTrack(hit.track.id)
    if (timelineTool.value === 'draw') {
      selectedClipIds.value = new Set([hit.clip.id])
      activeClipId.value = hit.clip.id
      if (hit.clip.type === 'midi') {
        openPiano()
        selectedNoteIds.value = new Set()
      }
      drawAll()
      return
    }
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      toggleClipSelection(hit.clip.id)
    } else if (!selectedClipIds.value.has(hit.clip.id)) {
      selectedClipIds.value = new Set([hit.clip.id])
    }
    activeClipId.value = hit.clip.id
    const movingIds = selectedClipIds.value.has(hit.clip.id)
      ? [...selectedClipIds.value]
      : [hit.clip.id]
    arrangementDrag = {
      type: hit.edge === 'right' ? 'resize' : 'move',
      canvas: 'body',
      pointerId: event.pointerId,
      startBeat: beat,
      startTrackIndex: hit.trackIndex,
      clipId: hit.clip.id,
      originals: cloneClipsByIds(movingIds),
    }
    bindArrangementDrag()
    drawAll()
    return
  }

  const index = arrangementTrackIndexAtY(y)
  const track = tracks.value[index]
  if (track) {
    selectTrack(track.id)
    if (timelineTool.value === 'draw' && isInstrumentTrack(track) && point) {
      selectedAutomationPoint.value = { trackId: null, index: -1 }
      selectedClipIds.value = new Set()
      await drawTimelineMidiAtPoint(point)
      return
    }
    if (isAutomationTrack(track) && point) {
      selectedClipIds.value = new Set()
      const hit = hitTestAutomationPoint(track, point.x, point.y, point.trackIndex)
      if (hit) {
        startAutomationPointDrag(track, hit.index, event.pointerId)
        return
      }
      const curveHit = hitTestAutomationCurveHandle(track, point.x, point.y, point.trackIndex)
      if (curveHit) {
        startAutomationCurveDrag(track, curveHit, point.y, event.pointerId)
        return
      }
      selectedAutomationPoint.value = { trackId: null, index: -1 }
      startAutomationDrag(track, point, event.pointerId)
      return
    }
    selectedAutomationPoint.value = { trackId: null, index: -1 }
    if (!event.ctrlKey && !event.metaKey && !event.shiftKey) {
      selectedClipIds.value = new Set()
    }
    drawAll()
  }
}

function onArrangementDoubleClick(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const hit = hitTestArrangementClip(
    event.clientX - rect.left,
    event.clientY - rect.top + arrangementTrackTop(0)
  )
  if (!hit) return
  selectTrack(hit.track.id)
  selectedClipIds.value = new Set([hit.clip.id])
  activeClipId.value = hit.clip.id
  if (hit.clip.type === 'midi') {
    openPiano()
    selectedNoteIds.value = new Set()
  }
  drawAll()
}

function bindArrangementDrag() {
  window.addEventListener('pointermove', onArrangementPointerMove)
  window.addEventListener('pointerup', onArrangementPointerUp)
}

function unbindArrangementDrag() {
  window.removeEventListener('pointermove', onArrangementPointerMove)
  window.removeEventListener('pointerup', onArrangementPointerUp)
}

function onArrangementPointerMove(event) {
  if (!arrangementDrag || !project.value) return
  if (arrangementDrag.type === 'pan') {
    const wrap = arrangementWrap.value
    if (!wrap) return
    const deltaX = event.clientX - arrangementDrag.startX
    if (Math.abs(deltaX) > 3) arrangementDrag.moved = true
    wrap.scrollLeft = arrangementDrag.startScrollLeft - deltaX
    return
  }

  const point = arrangementPoint(event)
  if (!point) return
  const deltaBeat = snapBeat(point.beat - arrangementDrag.startBeat)

  if (arrangementDrag.type === 'resize') {
    applyDraggedClips((original) => {
      if (original.clip.id !== arrangementDrag.clipId) return original
      return {
        ...original,
        clip: {
          ...original.clip,
          duration: Math.max(snapStep, snapBeat(point.beat - original.clip.start)),
        },
      }
    })
  } else {
    const deltaTrack = clamp(
      point.trackIndex - arrangementDrag.startTrackIndex,
      -tracks.value.length,
      tracks.value.length
    )
    applyDraggedClips((original) => ({
      ...original,
      trackIndex: clamp(original.trackIndex + deltaTrack, 0, tracks.value.length - 1),
      clip: {
        ...original.clip,
        start: Math.max(0, snapBeat(original.clip.start + deltaBeat)),
      },
    }))
  }
  drawAll()
}

async function onArrangementPointerUp() {
  if (!arrangementDrag) return
  if (arrangementDrag.type === 'pan') {
    const drag = arrangementDrag
    arrangementDrag = null
    unbindArrangementDrag()
    if (!drag.moved) {
      await seekToBeat(drag.startBeat)
    }
    return
  }

  const drag = arrangementDrag
  const clipIds = drag.originals.map(record => record.clip.id)
  const nextRecords = cloneClipsByIds(clipIds)
  const operations = buildClipDiffOperations(drag.originals, nextRecords)
  arrangementDrag = null
  unbindArrangementDrag()
  await diffClips(operations)
  drawAll()
}

function syncArrangementScroll(event) {
  arrangementScrollLeft.value = Math.max(0, Number(event.currentTarget?.scrollLeft || 0))
  closeTrackContextMenu()
}

function onArrangementWheel(event) {
  const canvas = arrangementCanvasForEvent(event)
  const wrap = arrangementWrap.value
  if (!canvas || !wrap) return
  const rect = canvas.getBoundingClientRect()
  let y = event.clientY - rect.top
  if (canvas === arrangementCanvas.value) y += arrangementTrackTop(0)
  if (y > arrangementRulerH) {
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
      scrollArrangementHorizontallyFromWheel(event, wrap)
    }
    if (!event.ctrlKey && !event.metaKey) return
  }

  event.preventDefault()
  const oldScale = arrangementPxPerBeat.value
  const zoom = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextScale = clamp(oldScale * zoom, minArrangementPxPerBeat, maxArrangementPxPerBeat)
  if (nextScale === oldScale) return

  const contentX = event.clientX - rect.left
  const wrapRect = wrap.getBoundingClientRect()
  const viewportX = event.clientX - wrapRect.left
  const canvasOffsetX = arrangementCanvasOffsetX()
  const beatAtCursor = Math.max(0, contentX / oldScale)
  arrangementPxPerBeat.value = nextScale
  drawAll()
  requestAnimationFrame(() => {
    const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
    wrap.scrollLeft = clamp(
      canvasOffsetX + (beatAtCursor * nextScale) - viewportX,
      0,
      maxScroll
    )
  })
}

function scrollArrangementHorizontallyFromWheel(event, wrap) {
  const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
  if (maxScroll <= 0) return
  const wheelDelta = event.deltaX || event.deltaY
  if (!wheelDelta) return
  event.preventDefault()
  wrap.scrollLeft = clamp(wrap.scrollLeft + wheelDelta, 0, maxScroll)
  arrangementScrollLeft.value = wrap.scrollLeft
}

function arrangementCanvasOffsetX() {
  return arrangementCanvas.value?.offsetLeft ?? currentTrackListWidth()
}

function arrangementCanvasForEvent(event) {
  if (event?.currentTarget === window && arrangementDrag?.canvas === 'header') return arrangementHeaderCanvas.value
  if (event?.currentTarget === window && arrangementDrag?.canvas === 'body') return arrangementCanvas.value
  const target = event?.currentTarget === window ? event?.target : event?.currentTarget
  if (target === arrangementHeaderCanvas.value) return arrangementHeaderCanvas.value
  if (target === arrangementCanvas.value) return arrangementCanvas.value
  if (arrangementDrag?.canvas === 'header') return arrangementHeaderCanvas.value
  return arrangementCanvas.value || arrangementHeaderCanvas.value
}

function arrangementPoint(event) {
  const canvas = arrangementCanvasForEvent(event)
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  let y = event.clientY - rect.top
  if (canvas === arrangementCanvas.value) y += arrangementTrackTop(0)
  const beat = Math.max(0, (x) / arrangementPxPerBeat.value)
  const trackIndex = clamp(
    arrangementTrackIndexAtY(y),
    0,
    Math.max(0, tracks.value.length - 1)
  )
  return { x, y, beat, trackIndex }
}

function hitTestArrangementClip(x, y) {
  if (y < arrangementTrackTop(0)) return null
  const trackIndex = arrangementTrackIndexAtY(y)
  const track = tracks.value[trackIndex]
  if (!track) return null
  const clips = [...(track.clips || [])].reverse()
  for (const clip of clips) {
    const rect = clipRect(clip, trackIndex)
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      return {
        track,
        trackIndex,
        clip,
        edge: x >= rect.x + rect.w - 8 ? 'right' : 'body',
      }
    }
  }
  return null
}

function clipRect(clip, trackIndex) {
  const scale = arrangementPxPerBeat.value
  return {
    x: Number(clip.start || 0) * scale + 2,
    y: arrangementTrackTop(trackIndex) + 10,
    w: Math.max(18, Number(clip.duration || 0.25) * scale - 4),
    h: arrangementTrackH - 20,
  }
}

function cloneClipsByIds(ids) {
  const idSet = new Set(ids)
  return tracks.value.flatMap((track, trackIndex) => (
    (track.clips || [])
      .filter(clip => idSet.has(clip.id))
      .map(clip => ({
        trackId: track.id,
        trackIndex,
        clip: {
          ...clip,
          notes: cloneNotes(clip.notes),
          events: cloneEvents(clip.events),
        },
      }))
  ))
}

function applyDraggedClips(mapper) {
  if (!arrangementDrag || !project.value) return
  const nextRecords = arrangementDrag.originals.map(mapper)
  const movedIds = new Set(arrangementDrag.originals.map(record => record.clip.id))
  for (const track of tracks.value) {
    track.clips = (track.clips || []).filter(clip => !movedIds.has(clip.id))
  }
  for (const record of nextRecords) {
    const track = tracks.value[record.trackIndex]
    if (!track) continue
    track.clips = [...(track.clips || []), record.clip].sort(sortClips)
  }
}

function sortClips(a, b) {
  return Number(a.start || 0) - Number(b.start || 0)
    || String(a.type).localeCompare(String(b.type))
    || String(a.name).localeCompare(String(b.name))
}

function cloneNotes(notes = []) {
  return (notes || []).map(note => ({ ...note }))
}

function cloneEvents(events = []) {
  return (events || []).map(event => ({ ...event }))
}

function toggleClipSelection(clipId) {
  const next = new Set(selectedClipIds.value)
  if (next.has(clipId)) next.delete(clipId)
  else next.add(clipId)
  selectedClipIds.value = next
}

function copySelectedClips() {
  const records = cloneClipsByIds([...selectedClipIds.value])
  if (!records.length) return
  const baseStart = Math.min(...records.map(record => Number(record.clip.start || 0)))
  clipClipboard.value = records.map(record => ({
    trackId: record.trackId,
    startOffset: Number(record.clip.start || 0) - baseStart,
    clip: {
      ...record.clip,
      notes: cloneNotes(record.clip.notes),
      events: cloneEvents(record.clip.events),
    },
  }))
}

async function pasteClips() {
  if (!clipClipboard.value.length || !activeTrack.value) return
  const pasteStart = snapBeat(Math.max(0, visualPositionBeats.value))
  const pastedIds = []
  const nextRecords = []
  for (const item of clipClipboard.value) {
    const track = tracks.value.find(candidate => Number(candidate.id) === Number(item.trackId))
      || activeTrack.value
    if (!track) continue
    const clip = {
      ...item.clip,
      id: makeClipId(),
      start: pasteStart + item.startOffset,
      notes: cloneNotes(item.clip.notes),
      events: cloneEvents(item.clip.events),
    }
    pastedIds.push(clip.id)
    nextRecords.push({ trackId: track.id, clip })
  }
  const operations = buildClipDiffOperations([], nextRecords)
  await diffClips(operations)
  selectedClipIds.value = new Set(pastedIds)
  const first = findClipRecord(pastedIds[0])
  if (first) {
    activeClipId.value = first.clip.id
    selectTrack(first.track.id)
    if (first.clip.type === 'midi') openPiano()
    else closePiano()
  }
}

async function deleteSelectedClips() {
  if (!selectedClipIds.value.size) return
  const deleting = new Set(selectedClipIds.value)
  const operations = [...deleting].map(clipId => ({ op: 'delete_clip', clip_id: clipId }))
  await diffClips(operations)
  if (deleting.has(activeClipId.value)) {
    activeClipId.value = null
    closePiano()
    selectedNoteIds.value = new Set()
  }
  selectedClipIds.value = new Set()
}

async function onPianoPointerDown(event) {
  if (!activeMidiClip.value) return
  const canvas = pianoCanvasForEvent(event)
  if (!canvas) return
  event.preventDefault()
  const point = pianoPoint(event)
  if (!point || point.x < pianoKeyW) return
  const dragCanvas = pianoDragCanvas(point)
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  if (point.ruler) {
    pianoDrag = {
      type: 'pan',
      canvas: dragCanvas,
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: pianoWrap.value?.scrollLeft || 0,
      startBeat: point.beat,
      moved: false,
    }
    bindPianoDrag()
    return
  }
  if (point.meterLane) {
    const meterHit = hitTestMeterEvent(point)
    if (meterHit && isMeterEventLabelHit(point, meterHit)) {
      openPianoMeterEditor(event, meterHit)
      return
    }
    if (pianoTool.value === 'draw' && meterHit) {
      startDrawMeterEventPress(event, point, meterHit)
      return
    }
    if (meterHit) {
      openPianoMeterEditor(event, meterHit)
      return
    }
    if (pianoTool.value === 'draw') {
      await upsertMeterEventAtBeat(point.beat)
    }
    return
  }
  if (point.harmonyLane) {
    const harmonyHit = hitTestHarmonyEvent(point)
    openPianoHarmonyEditor(event, harmonyHit || {
      event: { beat: point.beat, text: '' },
      index: -1,
    })
    return
  }
  const hit = hitTestPianoNote(point.x, point.y)

  if (hit) {
    if (pianoTool.value === 'draw') {
      startDrawNotePress(event, point, hit)
      return
    }
    const noteId = hit.note.id
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      toggleNoteSelection(noteId)
    } else if (!selectedNoteIds.value.has(noteId)) {
      selectedNoteIds.value = new Set([noteId])
    }
    const movingIds = selectedNoteIds.value.has(noteId)
      ? [...selectedNoteIds.value]
      : [noteId]
    pianoDrag = {
      type: hit.edge === 'right' ? 'resize' : 'move',
      canvas: dragCanvas,
      pointerId: event.pointerId,
      startBeat: point.beat,
      startPitch: point.pitch,
      noteId,
      noteStart: hit.note.start,
      originals: cloneNotesByIds(movingIds),
      originalNotes: cloneNotes(activeMidiClip.value.clip.notes),
    }
    bindPianoDrag()
    drawAll()
    return
  }

  if (pianoTool.value === 'draw') {
    const start = snapPianoBeat(point.beat)
    const note = {
      id: makeNoteId(),
      pitch: point.pitch,
      start,
      duration: activeNoteStep.value,
      velocity: DEFAULT_NOTE_VELOCITY,
    }
    draftNote.value = note
    pianoDrag = {
      type: 'draw',
      canvas: dragCanvas,
      pointerId: event.pointerId,
      startBeat: note.start,
      pitch: note.pitch,
    }
  } else {
    if (!event.ctrlKey && !event.metaKey && !event.shiftKey) {
      selectedNoteIds.value = new Set()
    }
    selectionBox.value = {
      x1: point.x,
      y1: point.y,
      x2: point.x,
      y2: point.y,
      append: event.ctrlKey || event.metaKey || event.shiftKey,
    }
    pianoDrag = {
      type: 'select',
      canvas: dragCanvas,
      pointerId: event.pointerId,
    }
  }
  bindPianoDrag()
  drawAll()
}

function bindPianoDrag() {
  window.addEventListener('pointermove', onPianoPointerMove)
  window.addEventListener('pointerup', onPianoPointerUp)
  window.addEventListener('pointercancel', onPianoPointerCancel)
}

function unbindPianoDrag() {
  window.removeEventListener('pointermove', onPianoPointerMove)
  window.removeEventListener('pointerup', onPianoPointerUp)
  window.removeEventListener('pointercancel', onPianoPointerCancel)
}

function schedulePianoLongPress(callback) {
  clearPianoLongPressTimer()
  pianoLongPressTimer = setTimeout(() => {
    pianoLongPressTimer = null
    callback()
  }, pianoDrawLongPressMs)
}

function clearPianoLongPressTimer() {
  if (!pianoLongPressTimer) return
  clearTimeout(pianoLongPressTimer)
  pianoLongPressTimer = null
}

function drawPressMoved(event, drag) {
  return Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > pianoDrawMoveTolerancePx
}

function currentLowerEditorPanelHeight() {
  const panelHeight = lowerEditorPanel.value?.getBoundingClientRect?.().height
  if (panelHeight) return clampLowerEditorPanelHeight(panelHeight)
  const stackHeight = editorStack.value?.clientHeight || 0
  return clampLowerEditorPanelHeight(stackHeight * 0.42)
}

function clampLowerEditorPanelHeight(height) {
  const stackHeight = editorStack.value?.clientHeight || 0
  if (!stackHeight) {
    return Math.round(Math.max(minLowerEditorPanelHeight, Number(height || 0)))
  }
  const maxHeight = Math.max(1, stackHeight - minArrangementPanelHeight)
  const minHeight = Math.min(minLowerEditorPanelHeight, maxHeight)
  return Math.round(clamp(Number(height || minHeight), minHeight, maxHeight))
}

function clampTrackListWidth(width) {
  return Math.round(clamp(Number(width || defaultTrackListWidth), minTrackListWidth, maxTrackListWidth))
}

function currentTrackListWidth() {
  return clampTrackListWidth(trackListWidth.value)
}

function startTrackListResize(event) {
  event.preventDefault()
  event.stopPropagation()
  const startWidth = currentTrackListWidth()
  trackListWidth.value = startWidth
  trackListResizeDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startWidth,
  }
  bindTrackListResize()
}

function bindTrackListResize() {
  window.addEventListener('pointermove', onTrackListResizeMove)
  window.addEventListener('pointerup', onTrackListResizeEnd)
  window.addEventListener('pointercancel', onTrackListResizeEnd)
}

function unbindTrackListResize() {
  window.removeEventListener('pointermove', onTrackListResizeMove)
  window.removeEventListener('pointerup', onTrackListResizeEnd)
  window.removeEventListener('pointercancel', onTrackListResizeEnd)
}

function onTrackListResizeMove(event) {
  if (!trackListResizeDrag) return
  event.preventDefault()
  const deltaX = event.clientX - trackListResizeDrag.startX
  trackListWidth.value = clampTrackListWidth(trackListResizeDrag.startWidth + deltaX)
  drawAll()
}

function onTrackListResizeEnd() {
  if (!trackListResizeDrag) return
  trackListResizeDrag = null
  unbindTrackListResize()
  nextTick(drawAll)
}

function startLowerEditorResize(event) {
  event.preventDefault()
  event.stopPropagation()
  const startHeight = currentLowerEditorPanelHeight()
  lowerEditorPanelHeight.value = startHeight
  lowerEditorResizeDrag = {
    pointerId: event.pointerId,
    startY: event.clientY,
    startHeight,
  }
  bindLowerEditorResize()
}

function bindLowerEditorResize() {
  window.addEventListener('pointermove', onLowerEditorResizeMove)
  window.addEventListener('pointerup', onLowerEditorResizeEnd)
}

function unbindLowerEditorResize() {
  window.removeEventListener('pointermove', onLowerEditorResizeMove)
  window.removeEventListener('pointerup', onLowerEditorResizeEnd)
}

function onLowerEditorResizeMove(event) {
  if (!lowerEditorResizeDrag) return
  event.preventDefault()
  const deltaY = event.clientY - lowerEditorResizeDrag.startY
  lowerEditorPanelHeight.value = clampLowerEditorPanelHeight(lowerEditorResizeDrag.startHeight - deltaY)
  drawAll()
}

function onLowerEditorResizeEnd() {
  if (!lowerEditorResizeDrag) return
  lowerEditorResizeDrag = null
  unbindLowerEditorResize()
  nextTick(drawAll)
}

function startDrawNotePress(event, point, hit) {
  const noteId = hit.note.id
  const movingIds = selectedNoteIds.value.has(noteId) ? [...selectedNoteIds.value] : [noteId]
  pianoDrag = {
    type: 'draw-note-press',
    canvas: pianoDragCanvas(point),
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startBeat: point.beat,
    startPitch: point.pitch,
    noteId,
    noteStart: hit.note.start,
    editType: hit.edge === 'right' ? 'resize' : 'move',
    movingIds,
    originals: cloneNotesByIds(movingIds),
    originalNotes: cloneNotes(activeMidiClip.value?.clip.notes || []),
    moved: false,
    lastPoint: null,
  }
  schedulePianoLongPress(() => activateDrawNotePressDrag())
  bindPianoDrag()
  drawAll()
}

function activateDrawNotePressDrag() {
  if (!pianoDrag || pianoDrag.type !== 'draw-note-press') return
  const drag = pianoDrag
  selectedNoteIds.value = new Set(drag.movingIds)
  pianoDrag = {
    type: drag.editType,
    canvas: drag.canvas,
    pointerId: drag.pointerId,
    startBeat: drag.startBeat,
    startPitch: drag.startPitch,
    noteId: drag.noteId,
    noteStart: drag.noteStart,
    originals: drag.originals,
    originalNotes: drag.originalNotes,
  }
  if (drag.lastPoint) applyPianoDragAtPoint(pianoDrag, drag.lastPoint)
  drawAll()
}

function startDrawMeterEventPress(event, point, meterHit) {
  const originalEvents = editableMeterEvents()
  pianoDrag = {
    type: 'draw-meter-event-press',
    canvas: pianoDragCanvas(point),
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startBeat: point.beat,
    eventIndex: meterHit.index,
    originalEvent: { ...meterHit.event },
    originalEvents,
    moved: false,
    lastPoint: null,
  }
  schedulePianoLongPress(() => activateDrawMeterEventPressDrag())
  bindPianoDrag()
  drawAll()
}

function activateDrawMeterEventPressDrag() {
  if (!pianoDrag || pianoDrag.type !== 'draw-meter-event-press') return
  const drag = pianoDrag
  pianoDrag = {
    type: 'meter-event-move',
    canvas: drag.canvas,
    pointerId: drag.pointerId,
    startBeat: drag.startBeat,
    eventIndex: drag.eventIndex,
    originalEvent: drag.originalEvent,
    originalEvents: drag.originalEvents,
  }
  if (drag.lastPoint) applyPianoDragAtPoint(pianoDrag, drag.lastPoint)
  drawAll()
}

function onPianoPointerMove(event) {
  if (!pianoDrag) return
  if (pianoDrag.type === 'pan') {
    const wrap = pianoWrap.value
    if (!wrap) return
    event.preventDefault()
    const deltaX = event.clientX - pianoDrag.startX
    if (Math.abs(deltaX) > 3) pianoDrag.moved = true
    wrap.scrollLeft = pianoDrag.startScrollLeft - deltaX
    return
  }

  const point = pianoPoint(event)
  if (!point) return

  if (pianoDrag.type === 'draw-note-press' || pianoDrag.type === 'draw-meter-event-press') {
    pianoDrag.lastPoint = point
    if (drawPressMoved(event, pianoDrag)) pianoDrag.moved = true
    return
  }

  applyPianoDragAtPoint(pianoDrag, point)
  drawAll()
}

function applyPianoDragAtPoint(drag, point) {
  if (drag.type === 'draw' && draftNote.value) {
    const end = Math.max(drag.startBeat + activeNoteStep.value, snapPianoBeat(point.beat))
    draftNote.value = {
      ...draftNote.value,
      pitch: point.pitch,
      duration: Math.max(activeNoteStep.value, end - drag.startBeat),
    }
  } else if (drag.type === 'select' && selectionBox.value) {
    selectionBox.value = {
      ...selectionBox.value,
      x2: point.x,
      y2: point.y,
    }
  } else if (drag.type === 'move') {
    const deltaBeat = snapPianoBeatDelta(point.beat - drag.startBeat)
    const deltaPitch = point.pitch - drag.startPitch
    applyDraggedNotes((note) => ({
      ...note,
      start: snapPianoBeat(Math.max(0, note.start + deltaBeat)),
      pitch: clamp(note.pitch + deltaPitch, minPitch, maxPitch),
    }))
  } else if (drag.type === 'resize') {
    applyDraggedNotes((note) => {
      if (note.id !== drag.noteId) return note
      const duration = snapPianoDuration(point.beat - drag.noteStart)
      return {
        ...note,
        duration,
      }
    })
  } else if (drag.type === 'meter-event-move') {
    moveMeterEventDrag(drag, point)
  }
}

async function onPianoPointerUp() {
  if (!pianoDrag || !activeMidiClip.value) return
  const drag = pianoDrag
  pianoDrag = null
  clearPianoLongPressTimer()
  unbindPianoDrag()

  if (drag.type === 'pan') {
    if (!drag.moved) {
      await seekToBeat(Number(activeMidiClip.value.clip.start || 0) + drag.startBeat)
    }
  } else if (drag.type === 'draw' && draftNote.value) {
    const note = { ...draftNote.value }
    draftNote.value = null
    selectedNoteIds.value = new Set([note.id])
    await persistActiveClipNotes([...activeMidiClip.value.clip.notes, note])
  } else if (drag.type === 'select' && selectionBox.value) {
    const ids = notesInSelection(selectionBox.value)
    selectedNoteIds.value = selectionBox.value.append
      ? new Set([...selectedNoteIds.value, ...ids])
      : new Set(ids)
    selectionBox.value = null
  } else if (drag.type === 'move' || drag.type === 'resize') {
    await persistActiveClipNotes(activeMidiClip.value.clip.notes, {
      previousNotes: drag.originalNotes,
    })
  } else if (drag.type === 'draw-note-press') {
    if (!drag.moved) await deletePianoNoteById(drag.noteId)
  } else if (drag.type === 'draw-meter-event-press') {
    if (!drag.moved) await deleteMeterEventAtIndex(drag.eventIndex)
  } else if (drag.type === 'meter-event-move') {
    await persistMeterEvents(project.value.meter_events || [])
  }
  drawAll()
}

function onPianoPointerCancel() {
  if (!pianoDrag) return
  pianoDrag = null
  draftNote.value = null
  selectionBox.value = null
  clearPianoLongPressTimer()
  unbindPianoDrag()
  drawAll()
}

function onPianoWheel(event) {
  const canvas = pianoCanvasForEvent(event)
  const wrap = pianoWrap.value
  if (!canvas || !wrap) return
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  let y = event.clientY - rect.top
  if (canvas === pianoCanvas.value) y += pianoNoteTop.value
  if (y > pianoRulerH || x < pianoKeyW) return

  event.preventDefault()
  const oldScale = pianoPxPerBeat.value
  const zoom = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextScale = clamp(oldScale * zoom, minPianoPxPerBeat, maxPianoPxPerBeat)
  if (nextScale === oldScale) return

  const beatAtCursor = Math.max(0, (x - pianoKeyW) / oldScale)
  const wrapRect = wrap.getBoundingClientRect()
  const viewportX = event.clientX - wrapRect.left
  pianoPxPerBeat.value = nextScale
  drawAll()
  requestAnimationFrame(() => {
    const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
    wrap.scrollLeft = clamp((pianoKeyW + beatAtCursor * nextScale) - viewportX, 0, maxScroll)
  })
}

function pianoCanvasForEvent(event) {
  if (event?.currentTarget === window && pianoDrag?.canvas === 'header') return pianoHeaderCanvas.value
  if (event?.currentTarget === window && pianoDrag?.canvas === 'body') return pianoCanvas.value
  const target = event?.currentTarget === window ? event?.target : event?.currentTarget
  if (target === pianoHeaderCanvas.value) return pianoHeaderCanvas.value
  if (target === pianoCanvas.value) return pianoCanvas.value
  if (pianoDrag?.canvas === 'header') return pianoHeaderCanvas.value
  return pianoCanvas.value || pianoHeaderCanvas.value
}

function pianoDragCanvas(point) {
  return point?.canvas === pianoHeaderCanvas.value ? 'header' : 'body'
}

function pianoPoint(event) {
  const canvas = pianoCanvasForEvent(event)
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  let y = event.clientY - rect.top
  if (canvas === pianoCanvas.value) y += pianoNoteTop.value
  const localBeat = Math.max(0, (x - pianoKeyW) / pianoPxPerBeat.value)
  const ruler = y < pianoRulerH
  const meterLane = pianoMeterLaneVisible.value
    && y >= pianoMeterLaneTop.value
    && y < pianoMeterLaneTop.value + pianoSubtrackH
  const harmonyLane = pianoHarmonyLaneVisible.value
    && y >= pianoHarmonyLaneTop.value
    && y < pianoHarmonyLaneTop.value + pianoSubtrackH
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const beat = meterLane || harmonyLane ? clipStart + localBeat : localBeat
  const row = Math.floor((y - pianoNoteTop.value) / pianoRowH)
  const pitch = clamp(maxPitch - row, minPitch, maxPitch)
  return { x, y, beat, localBeat, pitch, ruler, meterLane, harmonyLane, canvas }
}

function hitTestPianoNote(x, y) {
  const notes = [...(activeMidiClip.value?.clip.notes || [])].reverse()
  for (const note of notes) {
    if (note.pitch < minPitch || note.pitch > maxPitch) continue
    const rect = noteRect(note)
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      return {
        note,
        edge: x >= rect.x + rect.w - 7 ? 'right' : 'body',
      }
    }
  }
  return null
}

function hitTestMeterEvent(point) {
  if (!point?.meterLane) return null
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const events = editableMeterEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    const x = pianoKeyW + (Number(event.beat || 0) - clipStart) * pianoPxPerBeat.value
    const label = `${event.numerator}/${event.denominator}`
    const labelLeft = x + 4
    const labelRight = labelLeft + 8 + label.length * 7
    const markerHit = Math.abs(point.x - x) <= pianoMeterEventHitRadius
    const labelHit = point.x >= labelLeft - 2 && point.x <= labelRight
    if (markerHit || labelHit) {
      return { event, index }
    }
  }
  return null
}

function isMeterEventLabelHit(point, meterHit) {
  if (!point?.meterLane || !meterHit?.event) return false
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const x = pianoKeyW + (Number(meterHit.event.beat || 0) - clipStart) * pianoPxPerBeat.value
  const label = `${meterHit.event.numerator}/${meterHit.event.denominator}`
  const left = x + 4
  const right = left + 8 + label.length * 7
  return point.x >= left - 2
    && point.x <= right
    && point.y >= pianoMeterLaneTop.value
    && point.y <= pianoMeterLaneTop.value + pianoMeterLaneH
}

function hitTestHarmonyEvent(point) {
  if (!point?.harmonyLane) return null
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const events = editableHarmonyEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    const x = pianoKeyW + (Number(event.beat || 0) - clipStart) * pianoPxPerBeat.value
    const label = String(event.text || '')
    const labelLeft = x + 5
    const labelRight = labelLeft + 10 + label.length * 7
    const markerHit = Math.abs(point.x - x) <= pianoHarmonyEventHitRadius
    const labelHit = point.x >= labelLeft - 2 && point.x <= labelRight
    if (markerHit || labelHit) {
      return { event, index }
    }
  }
  return null
}

function openPianoMeterEditor(event, meterHit) {
  event.preventDefault()
  event.stopPropagation()
  const workspaceRect = pianoWorkspace.value?.getBoundingClientRect?.()
  const x = workspaceRect
    ? clamp(event.clientX - workspaceRect.left + 8, 8, Math.max(8, workspaceRect.width - 174))
    : 8
  const y = workspaceRect
    ? clamp(event.clientY - workspaceRect.top + 8, 8, Math.max(8, workspaceRect.height - 92))
    : pianoRulerH + 8
  pianoMeterEditor.value = {
    open: true,
    x,
    y,
    eventIndex: meterHit.index,
    beat: Number(meterHit.event.beat || 0),
    numerator: normalizeTimeSignatureNumerator(meterHit.event.numerator),
    denominator: normalizeTimeSignatureDenominator(meterHit.event.denominator),
  }
}

function openPianoHarmonyEditor(event, harmonyHit) {
  event.preventDefault()
  event.stopPropagation()
  const workspaceRect = pianoWorkspace.value?.getBoundingClientRect?.()
  const x = workspaceRect
    ? clamp(event.clientX - workspaceRect.left + 8, 8, Math.max(8, workspaceRect.width - 218))
    : 8
  const y = workspaceRect
    ? clamp(event.clientY - workspaceRect.top + 8, 8, Math.max(8, workspaceRect.height - 54))
    : pianoHarmonyLaneTop.value + 8
  pianoHarmonyEditor.value = {
    open: true,
    x,
    y,
    eventIndex: harmonyHit?.index ?? -1,
    beat: Number(harmonyHit?.event?.beat ?? 0),
    text: String(harmonyHit?.event?.text || ''),
  }
}

function closePianoMeterEditor() {
  if (!pianoMeterEditor.value.open) return
  pianoMeterEditor.value = {
    ...pianoMeterEditor.value,
    open: false,
  }
}

function closePianoHarmonyEditor() {
  if (!pianoHarmonyEditor.value.open) return
  pianoHarmonyEditor.value = {
    ...pianoHarmonyEditor.value,
    open: false,
  }
}

async function applyPianoMeterEditor() {
  const editor = pianoMeterEditor.value
  if (!editor.open || !project.value) return
  const numerator = normalizeTimeSignatureNumerator(editor.numerator)
  const denominator = normalizeTimeSignatureDenominator(editor.denominator)
  pianoMeterEditor.value = {
    ...editor,
    numerator,
    denominator,
  }
  await persistProjectUpdate((nextProject) => {
    if (isAtTimelineStart(editor.beat)) {
      nextProject.time_signature = [numerator, denominator]
    }
    const events = normalizeEditableMeterEvents(nextProject.meter_events || [])
    if (events[editor.eventIndex]) {
      events[editor.eventIndex] = {
        ...events[editor.eventIndex],
        numerator,
        denominator,
      }
      nextProject.meter_events = normalizeEditableMeterEvents(events)
    } else {
      upsertMeterEventInProject(nextProject, editor.beat, numerator, denominator)
    }
  })
}

async function applyPianoHarmonyEditor() {
  const editor = pianoHarmonyEditor.value
  if (!editor.open || !project.value) return
  const text = normalizeHarmonyText(editor.text)
  pianoHarmonyEditor.value = {
    ...editor,
    text,
  }
  if (!text && editor.eventIndex < 0) {
    closePianoHarmonyEditor()
    return
  }
  await persistProjectUpdate((nextProject) => {
    const events = normalizeEditableHarmonyEvents(nextProject.harmony_events || [])
    if (events[editor.eventIndex]) {
      if (text) {
        events[editor.eventIndex] = {
          ...events[editor.eventIndex],
          text,
        }
      } else {
        events.splice(editor.eventIndex, 1)
      }
    } else if (text) {
      events.push({
        beat: editor.beat,
        text,
      })
    }
    nextProject.harmony_events = normalizeEditableHarmonyEvents(events)
  })
  if (!text) closePianoHarmonyEditor()
}

function noteRect(note) {
  const scale = pianoPxPerBeat.value
  return {
    x: pianoKeyW + Number(note.start) * scale,
    y: pianoNoteTop.value + (maxPitch - Number(note.pitch)) * pianoRowH + 1,
    w: Math.max(8, Number(note.duration) * scale),
    h: pianoRowH - 2,
  }
}

function editableMeterEvents() {
  return normalizeEditableMeterEvents(project.value?.meter_events || [])
}

function editableHarmonyEvents() {
  return normalizeEditableHarmonyEvents(project.value?.harmony_events || [])
}

function normalizeEditableMeterEvents(events) {
  const byBeat = new Map()
  for (const rawEvent of Array.isArray(events) ? events : []) {
    const beat = Math.max(0, roundPianoBeat(rawEvent?.beat ?? rawEvent?.start ?? 0))
    byBeat.set(beat, {
      beat,
      numerator: normalizeTimeSignatureNumerator(rawEvent?.numerator),
      denominator: normalizeTimeSignatureDenominator(rawEvent?.denominator),
    })
  }
  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

function normalizeEditableHarmonyEvents(events) {
  const byBeat = new Map()
  for (const rawEvent of Array.isArray(events) ? events : []) {
    const text = normalizeHarmonyText(rawEvent?.text ?? rawEvent?.label ?? rawEvent?.chord)
    if (!text) continue
    const beat = Math.max(0, roundPianoBeat(rawEvent?.beat ?? rawEvent?.start ?? 0))
    byBeat.set(beat, { beat, text })
  }
  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

function normalizeHarmonyText(value) {
  return String(value || '').trim().slice(0, 64)
}

function roundPianoBeat(value) {
  return Math.round(Number(value || 0) * 1000000) / 1000000
}

function moveMeterEventDrag(drag, point) {
  if (!project.value) return
  const deltaBeat = snapPianoBeatDelta(point.beat - drag.startBeat)
  const nextBeat = Math.max(0, snapBeatToGrid(Number(drag.originalEvent.beat || 0) + deltaBeat, activePianoSnapStep.value))
  const nextEvents = drag.originalEvents.map((event, index) => (
    index === drag.eventIndex ? { ...event, beat: nextBeat } : { ...event }
  ))
  project.value.meter_events = normalizeEditableMeterEvents(nextEvents)
}

function cloneNotesByIds(ids) {
  const idSet = new Set(ids)
  return (activeMidiClip.value?.clip.notes || [])
    .filter(note => idSet.has(note.id))
    .map(note => ({ ...note }))
}

function applyDraggedNotes(mapper) {
  if (!activeMidiClip.value || !pianoDrag) return
  const originals = new Map(pianoDrag.originals.map(note => [note.id, note]))
  activeMidiClip.value.clip.notes = activeMidiClip.value.clip.notes
    .map(note => originals.has(note.id) ? mapper({ ...originals.get(note.id) }) : note)
    .sort(sortNotes)
}

async function persistActiveClipNotes(notes, options = {}) {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  const previous = (options.previousNotes || activeMidiClip.value.clip.notes)
    .map(normalizeClientNote)
    .sort(sortNotes)
  const normalized = notes.map(normalizeClientNote).sort(sortNotes)
  const operations = buildMidiNoteDiffOperations(previous, normalized, clipId)
  await diffMidi(activeMidiClip.value.track.id, operations)
}

function normalizeClientNote(note) {
  return {
    id: note.id || makeNoteId(),
    pitch: clamp(Math.round(Number(note.pitch || 60)), 0, 127),
    start: Math.max(0, snapBeatToGrid(Number(note.start || 0), null)),
    duration: Math.max(minFreehandStep, snapBeatToGrid(Number(note.duration || minFreehandStep), null)),
    velocity: clamp(Math.round(Number(note.velocity || DEFAULT_NOTE_VELOCITY)), 1, 127),
  }
}

function sortNotes(a, b) {
  return a.start - b.start || a.pitch - b.pitch || a.duration - b.duration
}

function notesInSelection(box) {
  const x1 = Math.min(box.x1, box.x2)
  const x2 = Math.max(box.x1, box.x2)
  const y1 = Math.min(box.y1, box.y2)
  const y2 = Math.max(box.y1, box.y2)
  return (activeMidiClip.value?.clip.notes || [])
    .filter((note) => {
      const rect = noteRect(note)
      return rect.x < x2 && rect.x + rect.w > x1 && rect.y < y2 && rect.y + rect.h > y1
    })
    .map(note => note.id)
}

function toggleNoteSelection(noteId) {
  const next = new Set(selectedNoteIds.value)
  if (next.has(noteId)) next.delete(noteId)
  else next.add(noteId)
  selectedNoteIds.value = next
}

function copySelectedNotes() {
  const selected = (activeMidiClip.value?.clip.notes || [])
    .filter(note => selectedNoteIds.value.has(note.id))
    .map(note => ({ ...note }))
  if (!selected.length) return
  const baseStart = Math.min(...selected.map(note => note.start))
  noteClipboard.value = selected.map(note => ({
    ...note,
    start: note.start - baseStart,
  }))
}

async function pasteNotes() {
  if (!activeMidiClip.value || !noteClipboard.value.length) return
  const pasteStart = snapPianoBeat(Math.max(0, visualPositionBeats.value))
  const clipStart = Number(activeMidiClip.value.clip.start || 0)
  const pasted = noteClipboard.value.map(note => ({
    ...note,
    id: makeNoteId(),
    start: Math.max(0, pasteStart - clipStart + note.start),
  }))
  selectedNoteIds.value = new Set(pasted.map(note => note.id))
  await persistActiveClipNotes([...activeMidiClip.value.clip.notes, ...pasted])
}

async function deleteSelectedNotes() {
  if (!activeMidiClip.value || selectedNoteIds.value.size === 0) return
  const selected = selectedNoteIds.value
  const remaining = activeMidiClip.value.clip.notes.filter(note => !selected.has(note.id))
  selectedNoteIds.value = new Set()
  await persistActiveClipNotes(remaining)
}

async function deletePianoNoteById(noteId) {
  if (!activeMidiClip.value) return
  const remaining = activeMidiClip.value.clip.notes.filter(note => note.id !== noteId)
  const nextSelection = new Set(selectedNoteIds.value)
  nextSelection.delete(noteId)
  selectedNoteIds.value = nextSelection
  await persistActiveClipNotes(remaining)
}

function setControllerWrap(el) {
  controllerWrap.value = el
}

function setTimeSignatureRoot(el) {
  timeSignatureRoot.value = el
}

function setControllerLaneCanvas(laneId, el) {
  if (el) controllerLaneCanvases.set(laneId, el)
  else controllerLaneCanvases.delete(laneId)
}

function syncPianoScroll(source) {
  if (syncingPianoScroll) return
  const from = source === 'piano' ? pianoWrap.value : controllerWrap.value
  const to = source === 'piano' ? controllerWrap.value : pianoWrap.value
  if (!from || !to) return
  controllerScrollLeft.value = source === 'controller' ? from.scrollLeft : to.scrollLeft
  if (to.scrollLeft === from.scrollLeft) return
  syncingPianoScroll = true
  to.scrollLeft = from.scrollLeft
  controllerScrollLeft.value = source === 'controller' ? from.scrollLeft : to.scrollLeft
  requestAnimationFrame(() => {
    syncingPianoScroll = false
  })
}

function controllerDefinitionForLane(lane) {
  return controllerDefinitionFromId(lane?.activeControllerId)
}

function controllerLabel(controllerId) {
  return controllerDefinitionFromId(controllerId).label
}

function controllerAxisTop(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).max)
}

function controllerAxisMiddle(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).middle)
}

function controllerAxisBottom(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).min)
}

function controllerMenuOptions(lane) {
  const existing = new Set(lane.controllerIds || [])
  return CONTROLLER_PRESETS.filter(preset => !existing.has(preset.id))
}

function toggleControllerMenu(laneId) {
  controllerMenuLaneId.value = controllerMenuLaneId.value === laneId ? null : laneId
  customControllerNumber.value = ''
}

function setLaneController(laneId, controllerId) {
  controllerLanes.value = controllerLanes.value.map((lane) => {
    if (lane.id !== laneId) return lane
    const controllerIds = lane.controllerIds.includes(controllerId)
      ? lane.controllerIds
      : [...lane.controllerIds, controllerId]
    return {
      ...lane,
      activeControllerId: controllerId,
      controllerIds,
    }
  })
  controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function addControllerLane() {
  controllerLanes.value = [
    ...controllerLanes.value,
    {
      id: makeControllerLaneId(),
      activeControllerId: 'cc:1',
      controllerIds: [...DEFAULT_CONTROLLER_IDS],
    },
  ]
  nextTick(drawAll)
}

function removeControllerLane(laneId) {
  if (controllerLanes.value.length <= 1) return
  controllerLanes.value = controllerLanes.value.filter(lane => lane.id !== laneId)
  if (controllerMenuLaneId.value === laneId) controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function addControllerToLane(laneId, controllerId) {
  const definition = controllerDefinitionFromId(controllerId)
  setLaneController(laneId, definition.id)
}

function addCustomControllerToLane(laneId) {
  const controller = Number(customControllerNumber.value)
  if (!Number.isFinite(controller)) return
  addControllerToLane(laneId, `cc:${clamp(Math.round(controller), 0, 127)}`)
  customControllerNumber.value = ''
}

function removeActiveControllerFromLane(laneId) {
  controllerLanes.value = controllerLanes.value.map((lane) => {
    if (lane.id !== laneId || lane.controllerIds.length <= 1) return lane
    const controllerIds = lane.controllerIds.filter(id => id !== lane.activeControllerId)
    return {
      ...lane,
      controllerIds,
      activeControllerId: controllerIds[0] || 'velocity',
    }
  })
  controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function controllerDefinitionFromEvent(event) {
  if (event?.type === 'control_change') {
    return controllerDefinitionFromId(`cc:${Number(event.controller || 0)}`)
  }
  if (event?.type === 'pitch_bend') return controllerDefinitionFromId('pitch_bend')
  if (event?.type === 'channel_pressure') return controllerDefinitionFromId('after_touch')
  return null
}

function normalizeEditableControllerEvent(event) {
  const definition = controllerDefinitionFromEvent(event)
  if (!definition) return { ...event }
  return normalizeControllerEvent(definition, event, null)
}

async function persistActiveClipEvents(events, options = {}) {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  const previous = (options.previousEvents || activeMidiClip.value.clip.events || [])
    .map(normalizeEditableControllerEvent)
    .sort(sortControllerEvents)
  const normalized = events
    .map(normalizeEditableControllerEvent)
    .sort(sortControllerEvents)
  const operations = buildMidiEventDiffOperations(previous, normalized, clipId)
  await diffMidi(activeMidiClip.value.track.id, operations)
}

function sortControllerEvents(a, b) {
  return Number(a.start || 0) - Number(b.start || 0)
    || String(a.type || '').localeCompare(String(b.type || ''))
    || Number(a.controller ?? a.pitch ?? -1) - Number(b.controller ?? b.pitch ?? -1)
    || String(a.id || '').localeCompare(String(b.id || ''))
}

function onControllerLanePointerDown(event, lane) {
  if (!activeMidiClip.value) return
  const point = controllerLanePoint(event)
  if (!point || point.x < pianoKeyW || point.y < controllerLaneTabH) return
  event.preventDefault()
  const definition = controllerDefinitionForLane(lane)
  const value = controllerValueFromY(point.y, definition)
  const originalNotes = cloneNotes(activeMidiClip.value.clip.notes || [])
  const originalEvents = cloneEvents(activeMidiClip.value.clip.events || [])

  if (definition.type === 'velocity') {
    selectedControllerEventId.value = null
    const note = findControllerVelocityNote(point.beat)
    if (!note) return
    updateNoteVelocity(note.id, value)
    controllerDrag = {
      type: 'velocity',
      laneId: lane.id,
      noteId: note.id,
      definition,
      originalNotes,
    }
  } else {
    const hit = hitTestControllerEvent(definition, point.x, point.y)
    if (hit) {
      selectedControllerEventId.value = hit.id
      controllerDrag = {
        type: 'event-point',
        laneId: lane.id,
        eventId: hit.id,
        definition,
        originalEvents,
      }
      bindControllerDrag()
      drawAll()
      return
    }
    const curveHit = hitTestControllerCurveHandle(definition, point.x, point.y)
    if (curveHit) {
      selectedControllerEventId.value = curveHit.eventId
      controllerDrag = {
        type: 'event-curve',
        laneId: lane.id,
        eventId: curveHit.eventId,
        definition,
        startY: point.y,
        startCurveAmount: Number(curveHit.event.curve_amount || 0),
        originalEvents,
      }
      bindControllerDrag()
      drawAll()
      return
    }
    const beat = snapControllerBeat(point.beat)
    const eventId = upsertControllerEventAtPoint(definition, beat, value)
    selectedControllerEventId.value = eventId
    controllerDrag = {
      type: 'event',
      laneId: lane.id,
      eventId,
      definition,
      lastBeat: beat,
      lastValue: value,
      originalEvents,
    }
  }

  bindControllerDrag()
  drawAll()
}

function bindControllerDrag() {
  window.addEventListener('pointermove', onControllerPointerMove)
  window.addEventListener('pointerup', onControllerPointerUp)
}

function unbindControllerDrag() {
  window.removeEventListener('pointermove', onControllerPointerMove)
  window.removeEventListener('pointerup', onControllerPointerUp)
}

function onControllerPointerMove(event) {
  if (!controllerDrag || !activeMidiClip.value) return
  const point = controllerLanePoint(event)
  if (!point) return
  event.preventDefault()
  const value = controllerValueFromY(point.y, controllerDrag.definition)
  if (controllerDrag.type === 'velocity') {
    updateNoteVelocity(controllerDrag.noteId, value)
  } else if (controllerDrag.type === 'event-curve') {
    const nextCurveAmount = normalizeCurveAmount(
      controllerDrag.startCurveAmount
        + ((controllerDrag.startY - point.y) / controllerLaneBodyH) * curveHandleDragScale
    )
    updateControllerEventCurve(controllerDrag.eventId, nextCurveAmount)
  } else if (controllerDrag.type === 'event-point') {
    const beat = snapControllerBeat(point.beat)
    updateControllerEvent(controllerDrag.definition, controllerDrag.eventId, { beat, value })
  } else {
    const beat = snapControllerBeat(point.beat)
    controllerDrag.eventId = writeControllerDragPoints(
      controllerDrag.definition,
      controllerDrag.lastBeat,
      controllerDrag.lastValue,
      beat,
      value
    )
    controllerDrag.lastBeat = beat
    controllerDrag.lastValue = value
  }
  drawAll()
}

async function onControllerPointerUp() {
  if (!controllerDrag || !activeMidiClip.value) return
  const drag = controllerDrag
  controllerDrag = null
  unbindControllerDrag()
  if (drag.type === 'velocity') {
    await persistActiveClipNotes(activeMidiClip.value.clip.notes, {
      previousNotes: drag.originalNotes,
    })
  } else {
    await persistActiveClipEvents(activeMidiClip.value.clip.events || [], {
      previousEvents: drag.originalEvents,
    })
  }
  drawAll()
}

function controllerLanePoint(event) {
  const target = event.target?.classList?.contains('controller-canvas')
    ? event.target
    : controllerLaneCanvases.get(controllerDrag?.laneId)
  const canvas = target || event.target
  if (!canvas?.getBoundingClientRect) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  return {
    x,
    y,
    beat: Math.max(0, (x - pianoKeyW) / pianoPxPerBeat.value),
  }
}

function controllerValueFromY(y, definition) {
  const bodyY = clamp(y - controllerLaneTabH, 0, controllerLaneBodyH)
  const unit = 1 - bodyY / controllerLaneBodyH
  return controllerUnitToValue(definition, unit)
}

function findControllerVelocityNote(beat) {
  const notes = activeMidiClip.value?.clip.notes || []
  const snapped = snapPianoBeat(beat)
  let closest = null
  let closestDistance = Number.POSITIVE_INFINITY
  for (const note of notes) {
    const start = Number(note.start || 0)
    const duration = Math.max(activeNoteStep.value, Number(note.duration || activeNoteStep.value))
    const distance = Math.min(Math.abs(start - beat), Math.abs(start + duration - beat))
    const inside = beat >= start - 0.05 && beat <= start + duration + 0.05
    if ((inside || Math.abs(start - snapped) < 0.001) && distance < closestDistance) {
      closest = note
      closestDistance = distance
    }
  }
  return closest
}

function updateNoteVelocity(noteId, value) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.notes = activeMidiClip.value.clip.notes
    .map(note => note.id === noteId
      ? { ...note, velocity: clamp(Math.round(value), 1, 127) }
      : note)
    .sort(sortNotes)
}

function upsertControllerEventAtPoint(definition, beat, value) {
  const events = activeMidiClip.value?.clip.events || []
  const start = snapControllerBeat(beat)
  const hit = findControllerEvent(definition, start)
  if (hit) {
    updateControllerEvent(definition, hit.id, { start, value })
    return hit.id
  }
  const event = normalizeControllerEvent(definition, {
    id: makeControllerEventId(),
    start,
    value,
  }, activePianoSnapStep.value)
  activeMidiClip.value.clip.events = [...events, event].sort(sortControllerEvents)
  return event.id
}

function writeControllerDragPoints(definition, startBeat, startValue, endBeat, endValue) {
  const beats = quantizedBeatsBetween(startBeat, endBeat, activePianoSnapStep.value)
  let lastEventId = controllerDrag?.eventId || null
  for (const beat of beats) {
    const value = interpolateControllerValue(startBeat, startValue, endBeat, endValue, beat)
    lastEventId = upsertControllerEventAtPoint(definition, beat, value)
  }
  return lastEventId
}

function findControllerEvent(definition, beat) {
  const events = activeMidiClip.value?.clip.events || []
  const snapThreshold = activePianoSnapStep.value
    ? Math.max(0.001, activePianoSnapStep.value / 3)
    : Number.POSITIVE_INFINITY
  const threshold = Math.min(Math.max(0.008, 3 / pianoPxPerBeat.value), snapThreshold)
  return events
    .filter(event => eventMatchesController(event, definition))
    .find(event => Math.abs(Number(event.start || 0) - beat) <= threshold)
}

function hitTestControllerEvent(definition, x, y) {
  const events = activeMidiClip.value?.clip.events || []
  for (const event of [...events].reverse()) {
    if (!eventMatchesController(event, definition)) continue
    const px = pianoKeyW + Number(event.start || 0) * pianoPxPerBeat.value
    const py = controllerValueToY(valueFromControllerEvent(event, definition), definition)
    if (Math.hypot(x - px, y - py) <= controllerPointHitRadius) {
      return event
    }
  }
  return null
}

function hitTestControllerCurveHandle(definition, x, y) {
  const points = controllerEditablePoints(definition)
  for (let index = points.length - 2; index >= 0; index -= 1) {
    const left = points[index]
    const right = points[index + 1]
    const handle = controllerCurveHandlePoint(left, right, definition)
    if (handle && Math.hypot(x - handle.x, y - handle.y) <= controllerCurveHandleHitRadius) {
      return {
        event: left.event,
        eventId: left.event.id,
        startBeat: left.start,
        endBeat: right.start,
      }
    }
  }
  return null
}

function controllerEditablePoints(definition) {
  const clip = activeMidiClip.value?.clip
  if (!clip) return []
  return controllerRenderPoints(clip.events || [], definition, 0)
    .filter(point => !point.synthetic)
}

function updateControllerEvent(definition, eventId, patch) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.events = (activeMidiClip.value.clip.events || [])
    .map((event) => {
      if (event.id !== eventId) return event
      const value = patch.value ?? valueFromControllerEvent(event, definition)
      const start = patch.beat ?? patch.start ?? event.start
      return normalizeControllerEvent(definition, { ...event, ...patch, start, value }, activePianoSnapStep.value)
    })
    .sort(sortControllerEvents)
}

function updateControllerEventCurve(eventId, curveAmount) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.events = (activeMidiClip.value.clip.events || [])
    .map((event) => {
      if (event.id !== eventId) return event
      return applyCurveAmount(event, curveAmount)
    })
    .sort(sortControllerEvents)
}

function setPianoQuantizeOption(optionId) {
  pianoQuantizeId.value = optionId
  timelineQuantizeMenuOpen.value = false
  pianoQuantizeMenuOpen.value = false
  drawAll()
}

function setTimelineTool(tool) {
  timelineTool.value = tool === 'draw' ? 'draw' : 'select'
}

function snapBeat(value) {
  return Math.round(Number(value || 0) / snapStep) * snapStep
}

function snapPianoBeat(value) {
  return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
}

function snapPianoBeatDelta(value) {
  return snapBeatToGrid(value, activePianoSnapStep.value)
}

function snapPianoDuration(value) {
  return Math.max(activeNoteStep.value, snapBeatToGrid(value, activePianoSnapStep.value))
}

function snapControllerBeat(value) {
  return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
}

function makeNoteId() {
  return `ui_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function makeClipId() {
  return `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function openPiano() {
  lowerEditorMode.value = 'piano'
  schedulePianoViewportFocus()
}

function schedulePianoViewportFocus() {
  nextTick(() => {
    drawAll()
    requestAnimationFrame(() => focusPianoViewport())
  })
}

function focusPianoViewport() {
  const wrap = pianoWrap.value
  const clip = activeMidiClip.value?.clip
  if (!wrap || !clip || !pianoVisible.value) return
  const scrollHeight = wrap.scrollHeight || pianoNoteTop.value + (maxPitch - minPitch + 1) * pianoRowH
  wrap.scrollTop = pianoScrollTopForNotes({
    notes: clip.notes || [],
    selectedNoteIds: selectedNoteIds.value,
    minPitch,
    maxPitch,
    rowHeight: pianoRowH,
    noteTop: pianoNoteTop.value,
    clientHeight: wrap.clientHeight,
    scrollHeight,
  })
}

function closePiano() {
  if (lowerEditorMode.value === 'piano') lowerEditorMode.value = null
  selectedNoteIds.value = new Set()
  selectedControllerEventId.value = null
  draftNote.value = null
  selectionBox.value = null
  lowerEditorResizeDrag = null
  controllerDrag = null
  cancelAutomationDrag()
  controllerMenuLaneId.value = null
  clearPianoLongPressTimer()
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  unbindPianoDrag()
  unbindLowerEditorResize()
  unbindControllerDrag()
  drawAll()
}

function openMixer() {
  lowerEditorMode.value = 'mixer'
  selectedNoteIds.value = new Set()
  selectedControllerEventId.value = null
  draftNote.value = null
  selectionBox.value = null
  timelineQuantizeMenuOpen.value = false
  pianoQuantizeMenuOpen.value = false
  controllerMenuLaneId.value = null
  clearPianoLongPressTimer()
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  unbindPianoDrag()
  unbindLowerEditorResize()
  unbindControllerDrag()
  drawAll()
}

function closeMixer() {
  if (lowerEditorMode.value === 'mixer') lowerEditorMode.value = null
  drawAll()
}

function isInstrumentTrack(track) {
  return (track?.type || 'instrument') === 'instrument'
}

function isAudioTrack(track) {
  return track?.type === 'audio'
}

function isBusTrack(track) {
  return track?.type === 'bus'
}

function isAutomationTrack(track) {
  return track?.type === 'automation'
}

function outputChainContainsTrack(bus, trackId) {
  if (trackId == null) return false
  const wantedId = Number(trackId)
  const seen = new Set()
  let outputId = bus?.output_bus_id
  while (outputId != null) {
    const numericOutputId = Number(outputId)
    if (numericOutputId === wantedId) return true
    if (seen.has(numericOutputId)) return false
    seen.add(numericOutputId)
    const nextBus = tracks.value.find(track => Number(track.id) === numericOutputId)
    outputId = nextBus?.output_bus_id
  }
  return false
}

function availableOutputBuses(trackId = null) {
  return tracks.value.filter(track => (
    isBusTrack(track)
    && (trackId == null || Number(track.id) !== Number(trackId))
    && !outputChainContainsTrack(track, trackId)
  ))
}

function canUseMixerInserts(track) {
  return isInstrumentTrack(track) || isBusTrack(track)
}

function insertSlotNumber(slotId) {
  const match = String(slotId || '').match(/^insert_(\d+)$/)
  return match ? Number(match[1]) : null
}

function nextInsertSlotId(track) {
  const used = new Set(
    (track?.plugin_slots || [])
      .map(slot => insertSlotNumber(slot.id))
      .filter(number => number !== null)
  )
  let nextNumber = 1
  while (used.has(nextNumber)) nextNumber += 1
  return `insert_${nextNumber}`
}

function mixerInsertSlots(track) {
  if (!canUseMixerInserts(track)) return []
  const slots = (track?.plugin_slots || [])
    .filter(slot => insertSlotNumber(slot.id) !== null)
    .sort((a, b) => insertSlotNumber(a.id) - insertSlotNumber(b.id))
  return [
    ...slots,
    { id: nextInsertSlotId(track), type: 'empty', name: 'Empty', addSlot: true },
  ]
}

function mixerPluginDisplayLabel(track, slot) {
  const current = pluginSlot(track, slot.id)
  if (current.type === 'empty') return slot.addSlot ? 'Add Insert' : 'Empty'
  return current.name || 'Plugin'
}

function mixerRouteKey(track) {
  return String(track?.id ?? 'master')
}

function mixerDuplicateRoutes() {
  return [...mixerTracks.value, masterBus.value]
}

function uniqueMixerPluginLabel(track, slot) {
  const baseLabel = mixerPluginDisplayLabel(track, slot)
  if (baseLabel === 'Add Insert' || baseLabel === 'Empty') return baseLabel
  const duplicates = []
  for (const mixerTrack of mixerDuplicateRoutes()) {
    for (const insertSlot of mixerInsertSlots(mixerTrack)) {
      if (insertSlot.addSlot) continue
      if (mixerPluginDisplayLabel(mixerTrack, insertSlot) === baseLabel) {
        duplicates.push(`${mixerRouteKey(mixerTrack)}:${insertSlot.id}`)
      }
    }
  }
  const duplicateIndex = duplicates.indexOf(`${mixerRouteKey(track)}:${slot.id}`)
  return duplicateIndex === 0 ? baseLabel : `${baseLabel} (${duplicateIndex})`
}

function mixerSendRows(track) {
  return Array.isArray(track?.sends) ? track.sends : []
}

function normalizedTrackSends(track) {
  return mixerSendRows(track).map(send => ({
    id: send.id || `send_${send.target_bus_id}`,
    target_bus_id: Number(send.target_bus_id),
    level: Number(send.level ?? 1),
    enabled: send.enabled !== false,
  }))
}

function updateTrackSend(track, index, patch) {
  const sends = normalizedTrackSends(track)
  if (!sends[index]) return null
  sends[index] = { ...sends[index], ...patch }
  if (sends[index].target_bus_id == null || Number.isNaN(Number(sends[index].target_bus_id))) {
    sends.splice(index, 1)
  }
  return updateTrack(track.id, { sends })
}

function addTrackSend(track, targetBusId) {
  const target = Number(targetBusId)
  if (!Number.isFinite(target)) return null
  const sends = normalizedTrackSends(track)
  if (sends.some(send => Number(send.target_bus_id) === target)) return null
  sends.push({ id: `send_${target}`, target_bus_id: target, level: 1, enabled: true })
  return updateTrack(track.id, { sends })
}

function removeTrackSend(track, index) {
  const sends = normalizedTrackSends(track)
  sends.splice(index, 1)
  return updateTrack(track.id, { sends })
}

function onAddTrackSendChange(track, event) {
  addTrackSend(track, event.target.value)
  event.target.value = ''
}

function updateMasterBus(patch) {
  return persistProjectUpdate(nextProject => {
    nextProject.master_bus = {
      ...normalizeMasterBus(nextProject.master_bus),
      ...patch,
    }
  })
}

function setMasterBusPlugin(plugin, slotId) {
  const slot = {
    ...(plugin || {}),
    id: slotId,
  }
  const pluginSlots = masterBus.value.plugin_slots
    .filter(existing => existing?.id !== slotId)
  return updateMasterBus({ plugin_slots: [slot, ...pluginSlots] })
}

function volumeDbLabel(volume) {
  const value = Number(volume ?? 0)
  if (value <= 0.0001) return '-inf dB'
  const db = 20 * Math.log10(value)
  return `${db >= 0 ? '+' : ''}${db.toFixed(1)} dB`
}

function updateTrackOutputBus(track, value) {
  return updateTrack(track.id, { output_bus_id: value ? Number(value) : null })
}

function trackChannelLabel(track) {
  return track?.channel_type === 'mono' ? 'Mono' : 'Multi-channel'
}

function trackTypeLabel(track) {
  if (isAutomationTrack(track)) return 'Automation'
  if (isBusTrack(track)) return 'Bus'
  return isAudioTrack(track) ? `Audio ${trackChannelLabel(track)}` : 'Instrument'
}

function trackRowMetaLabel(track) {
  if (isAutomationTrack(track)) {
    return `${trackTypeLabel(track)} / ${automationTargetLabel(track.target)}`
  }
  return `${trackTypeLabel(track)} / ${track.clips?.length || 0} clips`
}

function automationPointCount(track) {
  return Array.isArray(track?.automation?.points) ? track.automation.points.length : 0
}

function automationTargetLabel(target) {
  if (!target) return 'Unassigned'
  if (target.kind === 'unassigned') return target.label || 'Unassigned'
  if (target.kind === 'tempo_bpm') return target.label || 'Tempo BPM'
  if (target.kind === 'track_volume') return target.label || `Track ${target.track_id} Volume`
  if (target.kind === 'track_pan') return target.label || `Track ${target.track_id} Pan`
  return target.label || `Param ${target.param_index ?? 0}`
}

function learnedAutomationTargetDetail(item) {
  const source = item?.source || {}
  return [
    source.track_name,
    source.slot_label || source.slot_id,
    source.plugin_name,
    source.param_name,
  ].filter(Boolean).join(' / ')
}

function automationTargetForTrackVolume(track) {
  return {
    kind: 'track_volume',
    track_id: track.id,
    label: `${track.name} Volume`,
  }
}

function automationTargetForTrackPan(track) {
  return {
    kind: 'track_pan',
    track_id: track.id,
    label: `${track.name} Pan`,
  }
}

function automationTargetForTempoBpm() {
  return {
    kind: 'tempo_bpm',
    label: 'Tempo BPM',
  }
}

function automationTargetForPluginParameter(track, slotId, param) {
  return {
    kind: 'plugin_parameter',
    track_id: track.id,
    slot_id: slotId,
    param_index: Number(param.index || 0),
    param_id: param.param_id,
    label: param.name || `Parameter ${param.index || 0}`,
  }
}

function openAutomationMenu(event, target, label = '') {
  closeTrackContextMenu()
  automationMenu.value = {
    open: true,
    x: Number(event.clientX ?? 0),
    y: Number(event.clientY ?? 0),
    target,
    label: label || target?.label || 'Automation',
  }
}

async function confirmCreateAutomationFromMenu() {
  const target = automationMenu.value.target
  if (!target) return
  await createAutomationTrackForTarget(target)
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
}

async function createAutomationTrackForTarget(target, options = {}) {
  const value = automationInitialValue(target)
  return createAutomationTrack(target, {
    name: options.name || target.label || 'Automation',
    color: options.color,
    value,
    points: [
      { beat: Math.max(0, positionBeats.value), value },
      { beat: Math.max(1, positionBeats.value + 4), value },
    ],
  })
}

function automationInitialValue(target) {
  const track = tracks.value.find(item => Number(item.id) === Number(target?.track_id))
  if (target?.kind === 'tempo_bpm') {
    return Number(effectiveTempoAtBeat(project.value, visualPositionBeats.value) || 120)
  }
  if (target?.kind === 'track_volume') return Number(track?.volume ?? 0.8)
  if (target?.kind === 'track_pan') return Number(track?.pan ?? 0)
  if (target?.kind === 'plugin_parameter') {
    const param = pluginParameterRows(target.track_id, target.slot_id)
      .find(item => Number(item.index) === Number(target.param_index))
    return Number(param?.value ?? 0)
  }
  return 0
}

function pluginParameterRows(trackId, slotId) {
  return pluginParameters.value?.[`${trackId}:${slotId}`] || []
}

function parameterValueLabel(param) {
  const value = Number(param?.value ?? 0)
  const unit = param?.units ? ` ${param.units}` : ''
  return `${value.toFixed(3)}${unit}`
}

async function setLivePluginParameter(trackId, slotId, paramIndex, value) {
  await setPluginParameter(trackId, slotId, paramIndex, value)
}

function isPluginEditorOpen(trackId) {
  return Boolean(editorWindows.value?.[`${trackId}:instrument`]?.open)
}

function canOpenPluginEditor(track) {
  if (!isInstrumentTrack(track)) return false
  return pluginSlot(track, 'instrument').type === 'vst3'
}

async function togglePluginEditor(track) {
  selectTrack(track.id)
  if (!canOpenPluginEditor(track)) return
  try {
    await openPluginEditor(track.id, 'instrument')
  } catch {}
}

function pluginSlot(track, slotId = 'instrument') {
  const found = (track.plugin_slots || []).find(slot => slot.id === slotId)
  if (found) return found
  if (!isInstrumentTrack(track)) {
    return {
      id: slotId,
      type: 'empty',
      name: 'Empty',
    }
  }
  if (slotId !== 'instrument') {
    return {
      id: slotId,
      type: 'empty',
      name: 'Empty',
    }
  }
  return {
    id: 'instrument',
    type: 'builtin',
    name: track.instrument || 'ATRI Basic Synth',
  }
}

function pluginSlotValue(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (slot.type === 'vst3' && slot.path) return `vst3::${slot.path}`
  if (slot.type === 'vst2' && slot.path) return `vst2::${slot.path}`
  if (slot.type === 'empty') return 'empty::'
  return 'builtin::ATRI Basic Synth'
}

function selectedPluginMissing(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (!['vst3', 'vst2'].includes(slot.type) || !slot.path) return false
  const list = slot.type === 'vst3' ? pluginOptions.value.vst3 : pluginOptions.value.vst2
  return !list.some(plugin => plugin.path === slot.path)
}

function pluginSlotLabel(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (slot.type === 'vst3' && slotId !== 'instrument') {
    return `${slot.vendor || 'VST3'} / ${slot.category || 'Processor'}`
  }
  if (slot.type === 'vst3') return `${slot.vendor || 'VST3'} · ${slot.category || 'Instrument'}`
  if (slot.type === 'vst2') return 'VST2 scanned, loading pending'
  if (slot.type === 'empty') return 'No processor'
  return 'Internal test instrument'
}

function parsePluginValue(value) {
  const raw = String(value)
  const separator = raw.indexOf('::')
  if (separator === -1) return { type: 'empty', path: '' }
  return {
    type: raw.slice(0, separator),
    path: raw.slice(separator + 2),
  }
}

async function onPluginSelect(track, slotId, value) {
  const { type, path } = parsePluginValue(value)
  if (type === 'empty') {
    await setTrackPlugin(track.id, { id: slotId, type: 'empty', name: 'Empty' }, slotId)
    return
  }
  if (type === 'builtin') {
    await setTrackPlugin(
      track.id,
      { id: slotId, type: 'builtin', name: 'ATRI Basic Synth' },
      slotId
    )
    return
  }
  const plugin = [...pluginOptions.value.vst3, ...pluginOptions.value.vst2]
    .find(item => item.path === path)
  if (!plugin) return
  await setTrackPlugin(track.id, {
    ...plugin,
    id: slotId,
    type,
  }, slotId)
}

async function onMasterBusPluginSelect(slotId, value) {
  const { type, path } = parsePluginValue(value)
  if (type === 'empty' || type === 'builtin') {
    await setMasterBusPlugin({ id: slotId, type: 'empty', name: 'Empty' }, slotId)
    return
  }
  const plugin = [...pluginOptions.value.vst3, ...pluginOptions.value.vst2]
    .find(item => item.path === path)
  if (!plugin) return
  await setMasterBusPlugin({
    ...plugin,
    id: slotId,
    type,
  }, slotId)
}

function animationLoop(now) {
  if (!lastFrame) lastFrame = now
  const delta = (now - lastFrame) / 1000
  lastFrame = now
  if (playing.value) {
    visualPositionBeats.value += delta * (effectiveTempoAtBeat(project.value, visualPositionBeats.value) / 60)
    syncTransportDisplayFields(project.value)
    drawAll()
  } else if (visualPositionBeats.value !== positionBeats.value) {
    visualPositionBeats.value = positionBeats.value
    syncTransportDisplayFields(project.value)
    drawAll()
  }
  raf = requestAnimationFrame(animationLoop)
}

function drawAll() {
  if (!drawScheduler) {
    drawScheduler = createRafRedrawScheduler(drawAllNow)
  }
  drawScheduler.request()
}

function drawAllNow() {
  drawArrangement()
  drawPiano()
  drawControllerLanes()
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

onMounted(async () => {
  await loadProject()
  openFirstMidiClip()
  await refreshHostStatus()
  await loadPlugins()
  await nextTick()
  resizeObserver = new ResizeObserver(() => {
    if (lowerEditorPanelHeight.value) {
      lowerEditorPanelHeight.value = clampLowerEditorPanelHeight(lowerEditorPanelHeight.value)
    }
    drawAll()
  })
  if (editorStack.value) resizeObserver.observe(editorStack.value)
  if (arrangementWrap.value) resizeObserver.observe(arrangementWrap.value)
  if (pianoWrap.value) resizeObserver.observe(pianoWrap.value)
  if (controllerWrap.value) resizeObserver.observe(controllerWrap.value)
  raf = requestAnimationFrame(animationLoop)
  document.addEventListener('pointerdown', onDocumentPointerDown)
  learnedParameterPollTimer = setInterval(() => {
    if (host.value?.running) pollCapturedPluginParameters().catch(() => null)
  }, 1500)
})

onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  clearTimeout(tempoUpdateTimer)
  clearPianoLongPressTimer()
  clearInterval(learnedParameterPollTimer)
  if (resizeObserver) resizeObserver.disconnect()
  unbindPianoDrag()
  unbindLowerEditorResize()
  unbindTrackListResize()
  unbindArrangementDrag()
  unbindControllerDrag()
  cancelAutomationDrag()
  if (audioDecodeContext?.close) audioDecodeContext.close()
  disconnectAudioStream()
  if (drawScheduler) drawScheduler.cancel()
  cancelAnimationFrame(raf)
})

watch(project, (nextProject) => {
  syncPianoSubtrackLanes(nextProject)
  syncTransportDisplayFields(nextProject)
  if (activeClipId.value && !findClipRecord(activeClipId.value)) {
    activeClipId.value = null
    if (lowerEditorMode.value === 'piano') lowerEditorMode.value = null
    selectedNoteIds.value = new Set()
  }
  drawAll()
}, { immediate: true })
watch(activeTrack, () => {
  selectedNoteIds.value = new Set()
  drawAll()
})
watch(activeClipId, () => {
  if (pianoVisible.value && activeMidiClip.value) schedulePianoViewportFocus()
})
watch(positionBeats, (value) => {
  visualPositionBeats.value = value
  syncTransportDisplayFields(project.value)
  drawAll()
})
watch(exportFormat, (format) => {
  if (format === 'flac' && exportBitDepth.value === 'f32') {
    exportBitDepth.value = 'i24'
  }
})

watch(() => host.value.running, (running) => {
  if (running) {
    connectAudioStream()
  } else {
    disconnectAudioStream()
  }
}, { immediate: true })
</script>

<style scoped>
.studio-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  color: var(--t1);
  background: #17191c;
}

.tool-btn,
.mini-btn {
  height: 32px;
  min-width: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(229, 236, 245, 0.13);
  border-radius: 6px;
  background: #2b3035;
  color: var(--t2);
  cursor: pointer;
  transition: background 0.14s, border-color 0.14s, color 0.14s;
}

.mini-btn {
  width: 28px;
  height: 28px;
  min-width: 28px;
}

.tool-btn:hover,
.mini-btn:hover {
  color: var(--t1);
  background: #343b42;
  border-color: rgba(229, 236, 245, 0.22);
}

.tool-btn:disabled,
.mini-btn:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.tool-btn.primary {
  background: #0d74c9;
  border-color: #2588d5;
  color: white;
}

.tool-btn.active {
  color: #f0d17a;
  border-color: rgba(240, 209, 122, 0.34);
  background: rgba(240, 209, 122, 0.1);
}

.tool-btn.text,
.mini-btn.text {
  width: auto;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 650;
}

.mini-btn.active {
  color: #17191c;
  border-color: rgba(240, 209, 122, 0.72);
  background: #f0d17a;
}

.mini-btn.danger:hover {
  color: #ffd4cf;
  border-color: rgba(255, 141, 127, 0.42);
  background: rgba(255, 141, 127, 0.14);
}

.tool-btn svg,
.mini-btn svg {
  width: 15px;
  height: 15px;
}

.studio-error {
  padding: 8px 14px;
  background: rgba(255, 141, 127, 0.12);
  border-bottom: 1px solid rgba(255, 141, 127, 0.24);
  color: var(--red);
  font-family: var(--mono);
  font-size: 12px;
}

.studio-body {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 286px;
  overflow: hidden;
}

.studio-page.inspector-hidden .studio-body {
  grid-template-columns: minmax(0, 1fr);
}

.inspector {
  min-height: 0;
  overflow: auto;
  background: #202428;
}

.inspector {
  border-left: 1px solid rgba(229, 236, 245, 0.12);
}

.section-title {
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  color: var(--t3);
  font-size: 11px;
  text-transform: uppercase;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
  background: #262b30;
}

.editor-stack {
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-rows: minmax(220px, 1fr) minmax(180px, 42%);
  overflow: hidden;
  background: #17191c;
}

.studio-page.piano-closed .editor-stack {
  grid-template-rows: minmax(0, 1fr);
}

.piano-canvas-wrap {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.editor-canvas {
  display: block;
  min-width: 100%;
}

.piano-panel {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.piano-resize-handle {
  position: relative;
  flex: 0 0 8px;
  border-top: 1px solid rgba(229, 236, 245, 0.14);
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: #202428;
  cursor: ns-resize;
  touch-action: none;
}

.piano-resize-handle::before {
  content: '';
  position: absolute;
  inset: -5px 0;
}

.piano-resize-handle span {
  position: absolute;
  left: 50%;
  top: 3px;
  width: 42px;
  height: 2px;
  border-radius: 999px;
  background: rgba(229, 236, 245, 0.22);
  transform: translateX(-50%);
}

.piano-resize-handle:hover span {
  background: rgba(240, 209, 122, 0.72);
}

.piano-canvas-wrap {
  flex: 1 1 auto;
  position: relative;
  cursor: crosshair;
  overscroll-behavior: contain;
}

.piano-header-canvas {
  position: sticky;
  top: 0;
  z-index: 3;
  background: #17191c;
}

.piano-scroll-content {
  min-width: 100%;
}

.piano-workspace {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.piano-meter-popover {
  position: absolute;
  z-index: 24;
  width: 166px;
  display: grid;
  gap: 7px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 7px;
  background: #24282c;
  box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
}

.piano-meter-popover label {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.piano-meter-popover span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.piano-meter-popover input,
.piano-meter-popover select {
  height: 26px;
  min-width: 0;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 11px;
}

.piano-meter-popover input {
  width: 100%;
  padding: 0 7px;
}

.piano-meter-popover select {
  width: 100%;
  padding: 0 6px;
}

.piano-meter-popover input:focus,
.piano-meter-popover select:focus {
  outline: none;
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.piano-harmony-popover {
  position: absolute;
  z-index: 24;
  width: 210px;
  display: grid;
  gap: 7px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 7px;
  background: #24282c;
  box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
}

.piano-harmony-popover label {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.piano-harmony-popover span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.piano-harmony-popover input {
  width: 100%;
  height: 26px;
  min-width: 0;
  padding: 0 7px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 11px;
}

.piano-harmony-popover input:focus {
  outline: none;
  border-color: rgba(125, 168, 232, 0.5);
  box-shadow: 0 0 0 2px rgba(125, 168, 232, 0.12);
}

.piano-meter-toggle {
  position: absolute;
  right: 10px;
  z-index: 22;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid rgba(240, 209, 122, 0.28);
  border-radius: 4px;
  background: rgba(25, 29, 33, 0.82);
  color: #f0d17a;
  cursor: pointer;
}

.piano-meter-toggle::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 5px;
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 7px solid currentColor;
}

.piano-meter-toggle:hover,
.piano-meter-toggle:focus-visible {
  border-color: rgba(240, 209, 122, 0.62);
  background: rgba(40, 45, 50, 0.96);
  outline: none;
}

.piano-harmony-toggle {
  position: absolute;
  right: 10px;
  z-index: 22;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid rgba(125, 168, 232, 0.28);
  border-radius: 4px;
  background: rgba(25, 29, 33, 0.82);
  color: #b8d0ff;
  cursor: pointer;
}

.piano-harmony-toggle::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 5px;
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 7px solid currentColor;
}

.piano-harmony-toggle:hover,
.piano-harmony-toggle:focus-visible {
  border-color: rgba(125, 168, 232, 0.62);
  background: rgba(40, 45, 50, 0.96);
  outline: none;
}

.inspector-section {
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
}

.engine-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  background: rgba(229, 236, 245, 0.06);
}

.engine-stats div {
  padding: 9px 10px;
  background: #202428;
}

.engine-stats dt {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.engine-stats dd {
  margin-top: 2px;
  color: var(--t2);
  font-family: var(--mono);
  font-size: 12px;
}


@media (max-width: 1120px) {
  .studio-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .inspector {
    display: none;
  }

}

.studio-page.embedded {
  background: #17191c;
  border: 0;
}

.studio-page.embedded .studio-body {
  grid-template-columns: minmax(0, 1fr);
}

.studio-page.embedded .inspector {
  display: none;
}

.studio-page.embedded .editor-stack {
  grid-template-rows: minmax(118px, 44%) minmax(140px, 56%);
}

.studio-page.embedded.piano-closed .editor-stack {
  grid-template-rows: minmax(0, 1fr);
}

.studio-page.embedded .studio-error {
  padding: 6px 9px;
  font-size: 11px;
}
</style>
