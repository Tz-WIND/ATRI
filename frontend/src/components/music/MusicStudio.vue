<template>
  <div
    :class="[
      'studio-page',
      {
        embedded,
        'inspector-hidden': !inspectorVisible,
        'piano-closed': !pianoVisible || !activeMidiClip,
      },
    ]"
    tabindex="0"
    @keydown="onStudioKeydown"
  >
    <header class="studio-topbar">
      <div class="session-title">
        <span class="session-kicker">ATRI Studio</span>
        <strong>{{ project?.title || 'Session' }}</strong>
      </div>

      <div class="transport">
        <button
          class="tool-btn primary"
          :disabled="loading"
          :title="playing ? 'Pause' : 'Play'"
          @click="togglePlay"
        >
          <svg
            v-if="playing"
            viewBox="0 0 24 24"
            fill="currentColor"
          ><rect
            x="6"
            y="5"
            width="4"
            height="14"
          /><rect
            x="14"
            y="5"
            width="4"
            height="14"
          /></svg>
          <svg
            v-else
            viewBox="0 0 24 24"
            fill="currentColor"
          ><polygon points="7,4 19,12 7,20" /></svg>
        </button>
        <button
          class="tool-btn"
          title="Stop"
          @click="transport('stop')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="currentColor"
          ><rect
            x="6"
            y="6"
            width="12"
            height="12"
            rx="1"
          /></svg>
        </button>
        <div class="clock mono">
          {{ positionLabel }}
        </div>
        <div class="tempo-box mono">
          {{ Math.round(tempo) }} BPM
        </div>
      </div>

      <div class="host-controls">
        <span :class="['host-dot', { online: host.running, audio: audioConnected }]" />
        <span class="host-label">{{ host.running ? 'Host Online' : 'Host Offline' }}</span>
        <button
          class="tool-btn text"
          :disabled="syncing"
          @click="syncProject({ broadcast: true })"
        >
          Sync
        </button>
        <button
          class="tool-btn text"
          @click="resetDemo()"
        >
          Demo
        </button>
        <button
          :class="['tool-btn text', { active: inspectorVisible }]"
          title="Show or hide inspector"
          @click="inspectorVisible = !inspectorVisible"
        >
          Inspector
        </button>
      </div>
    </header>

    <div
      v-if="hostError"
      class="studio-error"
    >
      {{ hostError }}
    </div>

    <main class="studio-body">
      <aside class="track-list">
        <div class="track-list-head">
          <span>Tracks</span>
          <button
            class="mini-btn"
            title="Add Track"
            @click="createTrack('Instrument')"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            ><path d="M12 5v14M5 12h14" /></svg>
          </button>
        </div>

        <button
          v-for="track in tracks"
          :key="track.id"
          :class="['track-row', { active: activeTrack?.id === track.id }]"
          @click="selectTrack(track.id)"
        >
          <span
            class="track-color"
            :style="{ background: track.color }"
          />
          <span class="track-main">
            <strong>{{ track.name }}</strong>
            <small>{{ track.clips?.length || 0 }} clips / {{ track.notes.length }} notes</small>
          </span>
          <span class="track-buttons">
            <button
              :class="['track-flag', { on: track.mute }]"
              title="Mute"
              @click.stop="updateTrack(track.id, { mute: !track.mute })"
            >M</button>
            <button
              :class="['track-flag', { on: track.solo }]"
              title="Solo"
              @click.stop="updateTrack(track.id, { solo: !track.solo })"
            >S</button>
          </span>
        </button>
      </aside>

      <section class="editor-stack">
        <div class="arrangement">
          <div class="arrangement-toolbar">
            <div>
              <span>Timeline</span>
              <strong>{{ selectedClipIds.size }} selected</strong>
            </div>
            <div class="arrangement-actions">
              <button
                class="mini-btn text"
                title="Create MIDI clip at playhead"
                @click="createClip('midi')"
              >
                MIDI
              </button>
              <button
                class="mini-btn text"
                title="Create audio clip placeholder at playhead"
                @click="createClip('audio')"
              >
                Audio
              </button>
              <button
                class="mini-btn text"
                title="Copy selected clips"
                :disabled="selectedClipIds.size === 0"
                @click="copySelectedClips"
              >
                Copy
              </button>
              <button
                class="mini-btn text"
                title="Paste clips at playhead"
                :disabled="clipClipboard.length === 0"
                @click="pasteClips"
              >
                Paste
              </button>
              <button
                class="mini-btn text danger"
                title="Delete selected clips"
                :disabled="selectedClipIds.size === 0"
                @click="deleteSelectedClips"
              >
                Del
              </button>
            </div>
          </div>
          <div
            ref="arrangementWrap"
            class="arrangement-canvas-wrap"
          >
            <canvas
              ref="arrangementCanvas"
              class="editor-canvas"
              @dblclick="onArrangementDoubleClick"
              @pointerdown="onArrangementPointerDown"
              @contextmenu.prevent
            />
          </div>
        </div>

        <div
          v-if="pianoVisible && activeMidiClip"
          class="piano-panel"
        >
          <div class="piano-head">
            <div>
              <span>Piano Roll</span>
              <strong>{{ activeMidiClip.clip.name }}</strong>
            </div>
            <div class="piano-actions">
              <button
                :class="['mini-btn text', { active: pianoTool === 'select' }]"
                title="Select and move notes"
                @click="pianoTool = 'select'"
              >
                Select
              </button>
              <button
                :class="['mini-btn text', { active: pianoTool === 'draw' }]"
                title="Draw notes by dragging"
                @click="pianoTool = 'draw'"
              >
                Draw
              </button>
              <button
                class="mini-btn text"
                title="Copy selected notes"
                :disabled="selectedNoteIds.size === 0"
                @click="copySelectedNotes"
              >
                Copy
              </button>
              <button
                class="mini-btn text"
                title="Paste copied notes at the playhead"
                :disabled="noteClipboard.length === 0"
                @click="pasteNotes"
              >
                Paste
              </button>
              <button
                class="mini-btn text danger"
                title="Delete selected notes"
                :disabled="selectedNoteIds.size === 0"
                @click="deleteSelectedNotes"
              >
                Del
              </button>
              <button
                class="mini-btn text"
                title="Write C minor figure"
                @click="writeMinorFigure"
              >
                C minor
              </button>
              <button
                class="mini-btn text"
                title="Clear selected MIDI clip"
                @click="clearActiveTrack"
              >
                Clear
              </button>
              <button
                class="mini-btn"
                title="Close piano roll"
                @click="closePiano"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                ><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </div>
          </div>
          <div
            ref="pianoWrap"
            class="piano-canvas-wrap"
          >
            <canvas
              ref="pianoCanvas"
              class="editor-canvas"
              @pointerdown="onPianoPointerDown"
              @contextmenu.prevent
            />
          </div>
        </div>
      </section>

      <aside
        v-show="inspectorVisible"
        class="inspector"
      >
        <div class="inspector-section">
          <div class="section-title">
            Mixer
          </div>
          <div
            v-for="track in tracks"
            :key="`mix-${track.id}`"
            class="mix-strip"
          >
            <div class="mix-name">
              <span
                class="track-color"
                :style="{ background: track.color }"
              />
              <strong>{{ track.name }}</strong>
            </div>
            <label>
              <span>Vol</span>
              <input
                type="range"
                min="0"
                max="1.4"
                step="0.01"
                :value="track.volume"
                @change="updateTrack(track.id, { volume: Number($event.target.value) })"
              >
            </label>
            <label>
              <span>Pan</span>
              <input
                type="range"
                min="-1"
                max="1"
                step="0.01"
                :value="track.pan"
                @change="updateTrack(track.id, { pan: Number($event.target.value) })"
              >
            </label>
          </div>
        </div>

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
              <dt>Audio</dt>
              <dd>{{ audioConnected ? 'streaming' : 'idle' }}</dd>
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

        <div class="inspector-section plugin-rack">
          <div class="section-title rack-title">
            <span>Rack</span>
            <button
              class="rack-scan"
              :disabled="pluginsLoading"
              title="Scan VST plugins"
              @click="loadPlugins()"
            >
              {{ pluginsLoading ? 'Scanning' : 'Scan' }}
            </button>
          </div>
          <div
            v-for="track in tracks"
            :key="`rack-${track.id}`"
            :class="['rack-strip', { active: activeTrack?.id === track.id }]"
          >
            <div class="rack-strip-head">
              <span
                class="track-color"
                :style="{ background: track.color }"
              />
              <strong>{{ track.name }}</strong>
            </div>
            <div class="rack-slots">
              <label
                v-for="slot in rackSlots"
                :key="`${track.id}-${slot.id}`"
                :class="['rack-slot', { empty: pluginSlot(track, slot.id).type === 'empty' }]"
              >
                <span>{{ slot.label }}</span>
                <select
                  :value="pluginSlotValue(track, slot.id)"
                  @change="onPluginSelect(track, slot.id, $event.target.value)"
                >
                  <option
                    v-if="slot.id === 'instrument'"
                    value="builtin::ATRI Basic Synth"
                  >
                    ATRI Basic Synth
                  </option>
                  <option
                    v-else
                    value="empty::"
                  >
                    Empty
                  </option>
                  <option
                    v-for="plugin in pluginOptions.vst3"
                    :key="`${slot.id}-vst3-${plugin.path}`"
                    :value="`vst3::${plugin.path}`"
                  >
                    {{ plugin.name }}
                  </option>
                  <option
                    v-for="plugin in pluginOptions.vst2"
                    :key="`${slot.id}-vst2-${plugin.path}`"
                    :value="`vst2::${plugin.path}`"
                    disabled
                  >
                    {{ plugin.name }} (VST2)
                  </option>
                </select>
                <small>{{ pluginSlotLabel(track, slot.id) }}</small>
              </label>
            </div>
          </div>
        </div>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useDawHost } from '@/composables/useDawHost.js'

defineProps({
  embedded: { type: Boolean, default: false },
})

const {
  project,
  host,
  engine,
  tracks,
  activeTrack,
  loading,
  syncing,
  hostError,
  audioConnected,
  playing,
  positionBeats,
  totalNotes,
  plugins,
  pluginsLoading,
  loadProject,
  saveProject,
  syncProject,
  resetDemo,
  transport,
  updateTrack,
  createTrack,
  loadPlugins,
  setTrackPlugin,
  selectTrack,
  refreshHostStatus,
} = useDawHost()

const arrangementWrap = ref(null)
const arrangementCanvas = ref(null)
const pianoWrap = ref(null)
const pianoCanvas = ref(null)

const pxPerBeat = 56
const arrangementRulerH = 30
const arrangementTrackH = 62
const pianoKeyW = 76
const pianoRowH = 12
const minPitch = 36
const maxPitch = 84
const visualPositionBeats = ref(0)
const pianoTool = ref('select')
const selectedNoteIds = ref(new Set())
const selectedClipIds = ref(new Set())
const noteClipboard = ref([])
const clipClipboard = ref([])
const draftNote = ref(null)
const selectionBox = ref(null)
const activeClipId = ref(null)
const pianoVisible = ref(false)
const inspectorVisible = ref(true)
const rackSlots = [
  { id: 'instrument', label: 'Instrument' },
  { id: 'insert_1', label: 'Insert 1' },
  { id: 'insert_2', label: 'Insert 2' },
  { id: 'insert_3', label: 'Insert 3' },
  { id: 'insert_4', label: 'Insert 4' },
]

let resizeObserver = null
let raf = 0
let lastFrame = 0
let pianoDrag = null
let arrangementDrag = null

const snapStep = 0.25

const tempo = computed(() => Number(project.value?.tempo || 120))
const meterBeats = computed(() => Number(project.value?.time_signature?.[0] || 4))
const pluginOptions = computed(() => ({
  vst3: Array.isArray(plugins.value?.vst3) ? plugins.value.vst3 : [],
  vst2: Array.isArray(plugins.value?.vst2) ? plugins.value.vst2 : [],
}))
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
const positionLabel = computed(() => {
  const beats = Math.max(0, visualPositionBeats.value)
  const bar = Math.floor(beats / meterBeats.value) + 1
  const beat = Math.floor(beats % meterBeats.value) + 1
  const ticks = Math.floor((beats % 1) * 960)
  return `${bar.toString().padStart(5, '0')}.${beat.toString().padStart(2, '0')}.${ticks.toString().padStart(3, '0')}`
})

function cloneProject() {
  return JSON.parse(JSON.stringify(project.value || {}))
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

function makeClip(type = 'midi', start = 0) {
  const duration = 4
  return {
    id: makeClipId(),
    type,
    name: type === 'midi' ? 'MIDI Clip' : 'Audio Clip',
    start: snapBeat(start),
    duration,
    color: activeTrack.value?.color || '#4e79ff',
    source: '',
    path: '',
    notes: [],
  }
}

async function createClip(type = 'midi') {
  if (!activeTrack.value) return null
  const clip = makeClip(type, visualPositionBeats.value)
  await persistProjectUpdate((nextProject) => {
    const track = findProjectTrack(nextProject, activeTrack.value.id)
    if (!track) return
    track.clips = [...(track.clips || []), clip]
  })
  selectedClipIds.value = new Set([clip.id])
  activeClipId.value = clip.id
  if (type === 'midi') pianoVisible.value = true
  drawAll()
  return clip
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
    pianoVisible.value = true
    return
  }
}

async function togglePlay() {
  await transport(playing.value ? 'pause' : 'play')
}

async function writeMinorFigure() {
  if (!activeMidiClip.value) {
    await createClip('midi')
  }
  if (!activeMidiClip.value) return
  const notes = [60, 63, 67, 72, 70, 67, 63, 60].map((pitch, index) => ({
    pitch,
    start: index * 0.5,
    duration: 0.45,
    velocity: 82 + (index % 3) * 6,
  }))
  await persistActiveClipNotes(notes)
}

async function clearActiveTrack() {
  if (!activeMidiClip.value) return
  await persistActiveClipNotes([])
  selectedNoteIds.value = new Set()
}

async function onArrangementPointerDown(event) {
  const canvas = arrangementCanvas.value
  if (!canvas || !project.value) return
  event.preventDefault()
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const beat = Math.max(0, x / pxPerBeat)
  if (y <= arrangementRulerH) {
    await transport('seek', { position: (beat * 60) / tempo.value })
    visualPositionBeats.value = beat
    drawAll()
    return
  }

  const hit = hitTestArrangementClip(x, y)
  if (hit) {
    selectTrack(hit.track.id)
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

  const index = Math.floor((y - arrangementRulerH) / arrangementTrackH)
  const track = tracks.value[index]
  if (track) {
    selectTrack(track.id)
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
  const hit = hitTestArrangementClip(event.clientX - rect.left, event.clientY - rect.top)
  if (!hit) return
  selectTrack(hit.track.id)
  selectedClipIds.value = new Set([hit.clip.id])
  activeClipId.value = hit.clip.id
  if (hit.clip.type === 'midi') {
    pianoVisible.value = true
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
  arrangementDrag = null
  unbindArrangementDrag()
  await saveProject(project.value, { broadcast: true })
  drawAll()
}

function arrangementPoint(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const beat = Math.max(0, (x) / pxPerBeat)
  const trackIndex = clamp(
    Math.floor((y - arrangementRulerH) / arrangementTrackH),
    0,
    Math.max(0, tracks.value.length - 1)
  )
  return { x, y, beat, trackIndex }
}

function hitTestArrangementClip(x, y) {
  if (y <= arrangementRulerH) return null
  const trackIndex = Math.floor((y - arrangementRulerH) / arrangementTrackH)
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
  return {
    x: Number(clip.start || 0) * pxPerBeat + 2,
    y: arrangementRulerH + trackIndex * arrangementTrackH + 10,
    w: Math.max(18, Number(clip.duration || 0.25) * pxPerBeat - 4),
    h: arrangementTrackH - 20,
  }
}

function cloneClipsByIds(ids) {
  const idSet = new Set(ids)
  return tracks.value.flatMap((track, trackIndex) => (
    (track.clips || [])
      .filter(clip => idSet.has(clip.id))
      .map(clip => ({ trackId: track.id, trackIndex, clip: { ...clip, notes: cloneNotes(clip.notes) } }))
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
    },
  }))
}

async function pasteClips() {
  if (!clipClipboard.value.length || !activeTrack.value) return
  const pasteStart = snapBeat(Math.max(0, visualPositionBeats.value))
  const pastedIds = []
  await persistProjectUpdate((nextProject) => {
    for (const item of clipClipboard.value) {
      const track = findProjectTrack(nextProject, item.trackId)
        || findProjectTrack(nextProject, activeTrack.value.id)
      if (!track) continue
      const clip = {
        ...item.clip,
        id: makeClipId(),
        start: pasteStart + item.startOffset,
        notes: cloneNotes(item.clip.notes),
      }
      pastedIds.push(clip.id)
      track.clips = [...(track.clips || []), clip].sort(sortClips)
    }
  })
  selectedClipIds.value = new Set(pastedIds)
  const first = findClipRecord(pastedIds[0])
  if (first) {
    activeClipId.value = first.clip.id
    selectTrack(first.track.id)
    pianoVisible.value = first.clip.type === 'midi'
  }
}

async function deleteSelectedClips() {
  if (!selectedClipIds.value.size) return
  const deleting = new Set(selectedClipIds.value)
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      track.clips = (track.clips || []).filter(clip => !deleting.has(clip.id))
    }
  })
  if (deleting.has(activeClipId.value)) {
    activeClipId.value = null
    pianoVisible.value = false
    selectedNoteIds.value = new Set()
  }
  selectedClipIds.value = new Set()
}

function onPianoPointerDown(event) {
  if (!activeMidiClip.value) return
  const canvas = pianoCanvas.value
  if (!canvas) return
  event.preventDefault()
  const point = pianoPoint(event)
  if (!point || point.x < pianoKeyW) return
  const hit = hitTestPianoNote(point.x, point.y)

  if (hit) {
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
      pointerId: event.pointerId,
      startBeat: point.beat,
      startPitch: point.pitch,
      noteId,
      noteStart: hit.note.start,
      originals: cloneNotesByIds(movingIds),
    }
    bindPianoDrag()
    drawAll()
    return
  }

  if (pianoTool.value === 'draw') {
    const note = {
      id: makeNoteId(),
      pitch: point.pitch,
      start: snapBeat(point.beat),
      duration: snapStep,
      velocity: 96,
    }
    draftNote.value = note
    pianoDrag = {
      type: 'draw',
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
      pointerId: event.pointerId,
    }
  }
  bindPianoDrag()
  drawAll()
}

function bindPianoDrag() {
  window.addEventListener('pointermove', onPianoPointerMove)
  window.addEventListener('pointerup', onPianoPointerUp)
}

function unbindPianoDrag() {
  window.removeEventListener('pointermove', onPianoPointerMove)
  window.removeEventListener('pointerup', onPianoPointerUp)
}

function onPianoPointerMove(event) {
  if (!pianoDrag) return
  const point = pianoPoint(event)
  if (!point) return

  if (pianoDrag.type === 'draw' && draftNote.value) {
    const end = Math.max(pianoDrag.startBeat + snapStep, snapBeat(point.beat + snapStep))
    draftNote.value = {
      ...draftNote.value,
      pitch: point.pitch,
      duration: Math.max(snapStep, end - pianoDrag.startBeat),
    }
  } else if (pianoDrag.type === 'select' && selectionBox.value) {
    selectionBox.value = {
      ...selectionBox.value,
      x2: point.x,
      y2: point.y,
    }
  } else if (pianoDrag.type === 'move') {
    const deltaBeat = snapBeat(point.beat - pianoDrag.startBeat)
    const deltaPitch = point.pitch - pianoDrag.startPitch
    applyDraggedNotes((note) => ({
      ...note,
      start: Math.max(0, snapBeat(note.start + deltaBeat)),
      pitch: clamp(note.pitch + deltaPitch, minPitch, maxPitch),
    }))
  } else if (pianoDrag.type === 'resize') {
    applyDraggedNotes((note) => {
      if (note.id !== pianoDrag.noteId) return note
      const duration = snapBeat(point.beat - pianoDrag.noteStart)
      return {
        ...note,
        duration: Math.max(snapStep, duration),
      }
    })
  }
  drawAll()
}

async function onPianoPointerUp() {
  if (!pianoDrag || !activeMidiClip.value) return
  const drag = pianoDrag
  pianoDrag = null
  unbindPianoDrag()

  if (drag.type === 'draw' && draftNote.value) {
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
    await persistActiveClipNotes(activeMidiClip.value.clip.notes)
  }
  drawAll()
}

function pianoPoint(event) {
  const canvas = pianoCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const beat = Math.max(0, (x - pianoKeyW) / pxPerBeat)
  const row = Math.floor(y / pianoRowH)
  const pitch = clamp(maxPitch - row, minPitch, maxPitch)
  return { x, y, beat, pitch }
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

function noteRect(note) {
  return {
    x: pianoKeyW + Number(note.start) * pxPerBeat,
    y: (maxPitch - Number(note.pitch)) * pianoRowH + 1,
    w: Math.max(8, Number(note.duration) * pxPerBeat),
    h: pianoRowH - 2,
  }
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

async function persistActiveClipNotes(notes) {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  const normalized = notes.map(normalizeClientNote).sort(sortNotes)
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      const clip = (track.clips || []).find(item => item.id === clipId)
      if (!clip) continue
      clip.notes = normalized
      const noteEnd = Math.max(
        0,
        ...normalized.map(note => Number(note.start || 0) + Number(note.duration || 0))
      )
      clip.duration = Math.max(Number(clip.duration || 0.25), noteEnd, snapStep)
    }
  })
}

function normalizeClientNote(note) {
  return {
    id: note.id || makeNoteId(),
    pitch: clamp(Math.round(Number(note.pitch || 60)), 0, 127),
    start: Math.max(0, snapBeat(Number(note.start || 0))),
    duration: Math.max(snapStep, snapBeat(Number(note.duration || snapStep))),
    velocity: clamp(Math.round(Number(note.velocity || 96)), 1, 127),
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
  const pasteStart = snapBeat(Math.max(0, visualPositionBeats.value))
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

function onStudioKeydown(event) {
  const tag = String(event.target?.tagName || '').toLowerCase()
  if (['input', 'textarea', 'select', 'button'].includes(tag)) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
      copySelectedNotes()
    } else {
      copySelectedClips()
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && noteClipboard.value.length) {
      pasteNotes()
    } else {
      pasteClips()
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value) {
      selectedNoteIds.value = new Set(activeMidiClip.value.clip.notes.map(note => note.id))
    } else {
      selectedClipIds.value = new Set(
        tracks.value.flatMap(track => (track.clips || []).map(clip => clip.id))
      )
    }
    drawAll()
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
      deleteSelectedNotes()
    } else {
      deleteSelectedClips()
    }
  } else if (event.key === 'Escape') {
    selectedNoteIds.value = new Set()
    selectedClipIds.value = new Set()
    selectionBox.value = null
    draftNote.value = null
    drawAll()
  }
}

function snapBeat(value) {
  return Math.round(Number(value || 0) / snapStep) * snapStep
}

function makeNoteId() {
  return `ui_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function makeClipId() {
  return `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function closePiano() {
  pianoVisible.value = false
  selectedNoteIds.value = new Set()
  draftNote.value = null
  selectionBox.value = null
  drawAll()
}

function pluginSlot(track, slotId = 'instrument') {
  const found = (track.plugin_slots || []).find(slot => slot.id === slotId)
  if (found) return found
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

function animationLoop(now) {
  if (!lastFrame) lastFrame = now
  const delta = (now - lastFrame) / 1000
  lastFrame = now
  if (playing.value) {
    visualPositionBeats.value += delta * (tempo.value / 60)
  } else {
    visualPositionBeats.value = positionBeats.value
  }
  drawAll()
  raf = requestAnimationFrame(animationLoop)
}

function drawAll() {
  drawArrangement()
  drawPiano()
}

function setupCanvas(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.max(1, Math.floor(width * dpr))
  canvas.height = Math.max(1, Math.floor(height * dpr))
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return ctx
}

function drawArrangement() {
  const canvas = arrangementCanvas.value
  const wrap = arrangementWrap.value
  if (!canvas || !wrap) return
  const width = Math.max(wrap.clientWidth, arrangementLengthBeats() * pxPerBeat + 40)
  const height = Math.max(
    220,
    arrangementRulerH + Math.max(1, tracks.value.length) * arrangementTrackH
  )
  const ctx = setupCanvas(canvas, width, height)
  paintGrid(ctx, width, height, 0, arrangementRulerH)
  ctx.fillStyle = '#202326'
  ctx.fillRect(0, 0, width, arrangementRulerH)
  drawRuler(ctx, width)

  tracks.value.forEach((track, index) => {
    const y = arrangementRulerH + index * arrangementTrackH
    ctx.fillStyle = activeTrack.value?.id === track.id ? 'rgba(158, 191, 255, 0.08)' : '#1b1d20'
    ctx.fillRect(0, y, width, arrangementTrackH)
    ctx.strokeStyle = 'rgba(229, 236, 245, 0.11)'
    ctx.beginPath()
    ctx.moveTo(0, y + arrangementTrackH)
    ctx.lineTo(width, y + arrangementTrackH)
    ctx.stroke()

    for (const clip of track.clips || []) {
      drawArrangementClip(ctx, track, clip, index)
    }
  })
  drawPlayhead(ctx, height)
}

function arrangementLengthBeats() {
  const clipEnd = Math.max(
    0,
    ...tracks.value.flatMap(track => (track.clips || []).map((clip) => (
      Number(clip.start || 0) + Number(clip.duration || 0)
    )))
  )
  return Math.max(Number(project.value?.length_beats || 16), clipEnd + 2)
}

function drawArrangementClip(ctx, track, clip, trackIndex) {
  const rect = clipRect(clip, trackIndex)
  const selected = selectedClipIds.value.has(clip.id)
  const active = activeClipId.value === clip.id
  ctx.fillStyle = clip.type === 'audio'
    ? 'rgba(88, 167, 184, 0.68)'
    : hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.78)
  roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 5)
  ctx.fill()
  ctx.strokeStyle = active
    ? 'rgba(240, 209, 122, 0.95)'
    : selected ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.26)'
  ctx.lineWidth = active ? 2 : 1
  ctx.stroke()

  ctx.fillStyle = 'rgba(15,17,19,0.76)'
  ctx.fillRect(rect.x, rect.y, rect.w, 16)
  ctx.fillStyle = '#f4f6f8'
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText(
    `${clip.type === 'audio' ? 'AUDIO' : 'MIDI'}  ${clip.name || 'Clip'}`,
    rect.x + 7,
    rect.y + 11
  )

  if (clip.type === 'midi') {
    drawClipMidiPreview(ctx, clip, rect, track)
  } else {
    drawClipAudioPreview(ctx, rect)
  }

  ctx.fillStyle = 'rgba(255,255,255,0.35)'
  ctx.fillRect(rect.x + rect.w - 5, rect.y + 18, 2, rect.h - 24)
}

function drawClipMidiPreview(ctx, clip, rect, track) {
  const notes = clip.notes || []
  const minNote = Math.min(...notes.map(note => Number(note.pitch || 60)), 48)
  const maxNote = Math.max(...notes.map(note => Number(note.pitch || 60)), 72)
  const range = Math.max(1, maxNote - minNote)
  ctx.fillStyle = hexToRgba(track.color, 0.96)
  for (const note of notes) {
    const x = rect.x + (Number(note.start || 0) / Number(clip.duration || 1)) * rect.w
    const w = Math.max(3, (Number(note.duration || 0.25) / Number(clip.duration || 1)) * rect.w)
    const y = rect.y + 22 + (1 - (Number(note.pitch || 60) - minNote) / range) * (rect.h - 30)
    roundRect(ctx, x, y, Math.max(2, Math.min(w, rect.x + rect.w - x - 3)), 4, 2)
    ctx.fill()
  }
}

function drawClipAudioPreview(ctx, rect) {
  ctx.strokeStyle = 'rgba(255,255,255,0.55)'
  ctx.beginPath()
  const mid = rect.y + rect.h * 0.62
  for (let i = 0; i < Math.floor(rect.w); i += 6) {
    const x = rect.x + i
    const amp = 5 + ((i * 17) % 19)
    ctx.moveTo(x, mid - amp)
    ctx.lineTo(x, mid + amp)
  }
  ctx.stroke()
}

function drawPiano() {
  const canvas = pianoCanvas.value
  const wrap = pianoWrap.value
  if (!canvas || !wrap || !activeMidiClip.value || !pianoVisible.value) return
  const clip = activeMidiClip.value.clip
  const width = Math.max(
    wrap.clientWidth,
    pianoKeyW + (Number(clip.duration || 4) + 2) * pxPerBeat
  )
  const height = (maxPitch - minPitch + 1) * pianoRowH
  const ctx = setupCanvas(canvas, width, height)
  ctx.fillStyle = '#17191c'
  ctx.fillRect(0, 0, width, height)

  for (let pitch = maxPitch; pitch >= minPitch; pitch -= 1) {
    const row = maxPitch - pitch
    const y = row * pianoRowH
    const black = [1, 3, 6, 8, 10].includes(pitch % 12)
    ctx.fillStyle = black ? '#111316' : '#202326'
    ctx.fillRect(0, y, pianoKeyW, pianoRowH)
    ctx.fillStyle = black ? 'rgba(255,255,255,0.035)' : 'rgba(255,255,255,0.018)'
    ctx.fillRect(pianoKeyW, y, width - pianoKeyW, pianoRowH)
    ctx.strokeStyle = black ? 'rgba(0,0,0,0.38)' : 'rgba(229,236,245,0.08)'
    ctx.beginPath()
    ctx.moveTo(0, y + pianoRowH)
    ctx.lineTo(width, y + pianoRowH)
    ctx.stroke()
    if (pitch % 12 === 0) {
      ctx.fillStyle = '#9aa3ad'
      ctx.font = '10px Cascadia Mono, Consolas, monospace'
      ctx.fillText(pitchName(pitch), 10, y + 9)
    }
  }
  paintGrid(ctx, width, height, pianoKeyW, 0)

  const track = activeMidiClip.value.track
  if (track) {
    for (const note of clip.notes || []) {
      if (note.pitch < minPitch || note.pitch > maxPitch) continue
      const rect = noteRect(note)
      const selected = selectedNoteIds.value.has(note.id)
      ctx.fillStyle = selected ? '#f0d17a' : hexToRgba(track.color, 0.82)
      roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
      ctx.fill()
      ctx.strokeStyle = selected ? 'rgba(255, 255, 255, 0.64)' : 'rgba(0,0,0,0.24)'
      ctx.stroke()
      if (rect.w > 34) {
        ctx.fillStyle = selected ? 'rgba(20,22,24,0.9)' : 'rgba(255,255,255,0.82)'
        ctx.font = '10px Cascadia Mono, Consolas, monospace'
        ctx.fillText(pitchName(note.pitch), rect.x + 5, rect.y + 9)
      }
    }
  }
  if (draftNote.value) {
    const rect = noteRect(draftNote.value)
    ctx.fillStyle = 'rgba(240, 209, 122, 0.52)'
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.96)'
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
    ctx.fill()
    ctx.stroke()
  }
  if (selectionBox.value) {
    const box = selectionBox.value
    const x = Math.min(box.x1, box.x2)
    const y = Math.min(box.y1, box.y2)
    const w = Math.abs(box.x2 - box.x1)
    const h = Math.abs(box.y2 - box.y1)
    ctx.fillStyle = 'rgba(125, 168, 232, 0.12)'
    ctx.strokeStyle = 'rgba(125, 168, 232, 0.72)'
    ctx.setLineDash([4, 3])
    ctx.strokeRect(x, y, w, h)
    ctx.fillRect(x, y, w, h)
    ctx.setLineDash([])
  }
  drawPianoPlayhead(ctx, height, clip)
}

function drawRuler(ctx, width) {
  const bars = Math.ceil(width / (pxPerBeat * meterBeats.value))
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  for (let bar = 0; bar <= bars; bar += 1) {
    const x = bar * meterBeats.value * pxPerBeat
    ctx.fillStyle = '#9aa3ad'
    ctx.fillText(String(bar + 1), x + 5, 19)
  }
}

function paintGrid(ctx, width, height, offsetX, offsetY) {
  const beats = Math.ceil((width - offsetX) / pxPerBeat)
  for (let beat = 0; beat <= beats; beat += 1) {
    const x = offsetX + beat * pxPerBeat
    const isBar = beat % meterBeats.value === 0
    ctx.strokeStyle = isBar ? 'rgba(229,236,245,0.18)' : 'rgba(229,236,245,0.07)'
    ctx.lineWidth = isBar ? 1 : 0.5
    ctx.beginPath()
    ctx.moveTo(x, offsetY)
    ctx.lineTo(x, height)
    ctx.stroke()

    ctx.strokeStyle = 'rgba(229,236,245,0.035)'
    for (let div = 1; div < 4; div += 1) {
      const subX = x + (div * pxPerBeat) / 4
      ctx.beginPath()
      ctx.moveTo(subX, offsetY)
      ctx.lineTo(subX, height)
      ctx.stroke()
    }
  }
}

function drawPlayhead(ctx, height, offsetX = 0) {
  const x = offsetX + visualPositionBeats.value * pxPerBeat
  ctx.strokeStyle = '#d7b66f'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(x, 0)
  ctx.lineTo(x, height)
  ctx.stroke()
}

function drawPianoPlayhead(ctx, height, clip) {
  const localBeat = visualPositionBeats.value - Number(clip.start || 0)
  if (localBeat < 0 || localBeat > Number(clip.duration || 0)) return
  const x = pianoKeyW + localBeat * pxPerBeat
  ctx.strokeStyle = '#d7b66f'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(x, 0)
  ctx.lineTo(x, height)
  ctx.stroke()
}

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + width, y, x + width, y + height, r)
  ctx.arcTo(x + width, y + height, x, y + height, r)
  ctx.arcTo(x, y + height, x, y, r)
  ctx.arcTo(x, y, x + width, y, r)
  ctx.closePath()
}

function hexToRgba(hex, alpha) {
  const safe = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#4e79ff'
  const value = parseInt(safe.slice(1), 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function pitchName(pitch) {
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  return `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`
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
  resizeObserver = new ResizeObserver(drawAll)
  if (arrangementWrap.value) resizeObserver.observe(arrangementWrap.value)
  if (pianoWrap.value) resizeObserver.observe(pianoWrap.value)
  raf = requestAnimationFrame(animationLoop)
})

onUnmounted(() => {
  if (resizeObserver) resizeObserver.disconnect()
  unbindPianoDrag()
  unbindArrangementDrag()
  cancelAnimationFrame(raf)
})

watch(project, () => {
  if (activeClipId.value && !findClipRecord(activeClipId.value)) {
    activeClipId.value = null
    pianoVisible.value = false
    selectedNoteIds.value = new Set()
  }
  drawAll()
})
watch(activeTrack, () => {
  selectedNoteIds.value = new Set()
  drawAll()
})
watch(positionBeats, (value) => {
  visualPositionBeats.value = value
  drawAll()
})
</script>

<style scoped>
.studio-page {
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

.studio-topbar {
  height: 54px;
  display: grid;
  grid-template-columns: minmax(170px, 1fr) auto minmax(300px, 1fr);
  align-items: center;
  gap: 14px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.12);
  background: #24282c;
}

.session-title {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.session-kicker {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--orange);
  letter-spacing: 0;
  font-family: var(--mono);
}

.session-title strong {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.transport,
.host-controls,
.piano-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.host-controls {
  justify-content: flex-end;
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

.clock {
  min-width: 132px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px;
  color: #f0d17a;
  background: #141618;
  border: 1px solid rgba(240, 209, 122, 0.18);
  border-radius: 6px;
  font-size: 14px;
}

.tempo-box {
  height: 32px;
  display: flex;
  align-items: center;
  padding: 0 9px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 6px;
  color: var(--t3);
  background: #1d2024;
  font-size: 11px;
}

.host-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--red);
  box-shadow: 0 0 0 3px rgba(255, 141, 127, 0.12);
}

.host-dot.online {
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(143, 216, 199, 0.12);
}

.host-dot.audio {
  background: #f0d17a;
  box-shadow: 0 0 0 3px rgba(240, 209, 122, 0.14);
}

.host-label {
  color: var(--t3);
  font-size: 12px;
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
  grid-template-columns: 246px minmax(0, 1fr) 286px;
  overflow: hidden;
}

.studio-page.inspector-hidden .studio-body {
  grid-template-columns: 246px minmax(0, 1fr);
}

.track-list,
.inspector {
  min-height: 0;
  overflow: auto;
  background: #202428;
}

.track-list {
  border-right: 1px solid rgba(229, 236, 245, 0.12);
}

.inspector {
  border-left: 1px solid rgba(229, 236, 245, 0.12);
}

.track-list-head,
.arrangement-toolbar,
.piano-head,
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

.arrangement-toolbar {
  flex: 0 0 auto;
  text-transform: none;
}

.arrangement-toolbar div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.arrangement-toolbar span {
  color: var(--t3);
  text-transform: uppercase;
  font-size: 11px;
}

.arrangement-toolbar strong {
  color: var(--t1);
  font-size: 12px;
}

.arrangement-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.track-row {
  width: 100%;
  height: 62px;
  display: grid;
  grid-template-columns: 4px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 0 9px;
  border: 0;
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: transparent;
  color: var(--t2);
  cursor: pointer;
}

.track-row.active {
  background: rgba(158, 191, 255, 0.11);
  color: var(--t1);
}

.track-color {
  width: 4px;
  height: 24px;
  border-radius: 2px;
  flex: 0 0 auto;
}

.track-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.track-main strong,
.mix-name strong {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.track-main small {
  color: var(--t4);
  font-size: 11px;
}

.track-buttons {
  display: flex;
  gap: 4px;
}

.track-flag {
  width: 24px;
  height: 24px;
  border: 1px solid rgba(229, 236, 245, 0.1);
  border-radius: 5px;
  background: #181b1f;
  color: var(--t4);
  font-family: var(--mono);
  font-size: 10px;
  cursor: pointer;
}

.track-flag.on {
  color: #f0d17a;
  border-color: rgba(240, 209, 122, 0.32);
  background: rgba(240, 209, 122, 0.12);
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

.arrangement-canvas-wrap,
.piano-canvas-wrap {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.arrangement {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid rgba(229, 236, 245, 0.14);
}

.arrangement-canvas-wrap {
  flex: 1 1 auto;
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

.piano-canvas-wrap {
  flex: 1 1 auto;
  cursor: crosshair;
}

.piano-head {
  flex: 0 0 auto;
  text-transform: none;
}

.piano-head div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.piano-head span {
  color: var(--t3);
  text-transform: uppercase;
  font-size: 11px;
}

.piano-head strong {
  color: var(--t1);
  font-size: 12px;
}

.inspector-section {
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
}

.mix-strip {
  padding: 10px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.07);
}

.mix-name,
.rack-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mix-strip label {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  color: var(--t4);
  font-size: 11px;
}

.mix-strip input[type='range'] {
  width: 100%;
  accent-color: #9ebfff;
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

.plugin-rack {
  padding-bottom: 10px;
}

.rack-title {
  gap: 8px;
}

.rack-scan {
  height: 24px;
  padding: 0 8px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 5px;
  background: #191d21;
  color: var(--t3);
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
}

.rack-scan:hover {
  color: var(--t1);
  border-color: rgba(240, 209, 122, 0.3);
}

.rack-strip {
  margin: 8px 10px 0;
  padding: 9px;
  border: 1px solid rgba(229, 236, 245, 0.1);
  border-radius: 6px;
  background: #181b1f;
  color: var(--t2);
  font-size: 12px;
}

.rack-strip.active {
  border-color: rgba(240, 209, 122, 0.22);
  background: #1d2024;
}

.rack-strip-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rack-strip-head strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.rack-slots {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 9px;
}

.rack-slot {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  align-items: center;
  gap: 4px 8px;
}

.rack-slot span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.rack-slot select {
  min-width: 0;
  width: 100%;
  height: 28px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 5px;
  background: #101215;
  color: var(--t2);
  font-size: 11px;
}

.rack-slot.empty select {
  color: var(--t4);
}

.rack-slot small,
.rack-meta {
  grid-column: 2;
  color: var(--t4);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1120px) {
  .studio-topbar {
    grid-template-columns: 1fr;
    height: auto;
    padding: 10px;
  }

  .host-controls {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .studio-body {
    grid-template-columns: 190px minmax(0, 1fr);
  }

  .inspector {
    display: none;
  }
}

.studio-page.embedded {
  background: #17191c;
  border: 0;
}

.studio-page.embedded .studio-topbar {
  height: auto;
  min-height: 104px;
  grid-template-columns: 1fr;
  align-items: stretch;
  gap: 8px;
  padding: 9px;
  background: #202428;
}

.studio-page.embedded .session-title {
  min-width: 0;
}

.studio-page.embedded .session-title strong {
  font-size: 13px;
}

.studio-page.embedded .transport {
  justify-content: space-between;
  gap: 6px;
}

.studio-page.embedded .clock {
  min-width: 0;
  flex: 1;
  padding: 0 7px;
  font-size: 12px;
}

.studio-page.embedded .tempo-box {
  display: none;
}

.studio-page.embedded .host-controls {
  justify-content: space-between;
  gap: 6px;
}

.studio-page.embedded .host-label {
  display: none;
}

.studio-page.embedded .tool-btn.text {
  padding: 0 8px;
  font-size: 11px;
}

.studio-page.embedded .studio-body {
  grid-template-columns: minmax(0, 1fr);
}

.studio-page.embedded .track-list,
.studio-page.embedded .inspector {
  display: none;
}

.studio-page.embedded .editor-stack {
  grid-template-rows: minmax(118px, 44%) minmax(140px, 56%);
}

.studio-page.embedded.piano-closed .editor-stack {
  grid-template-rows: minmax(0, 1fr);
}

.studio-page.embedded .piano-head {
  height: 32px;
  padding: 0 8px;
}

.studio-page.embedded .piano-head div:first-child {
  min-width: 0;
}

.studio-page.embedded .piano-head strong {
  max-width: 112px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.studio-page.embedded .piano-actions {
  gap: 4px;
  overflow: auto;
}

.studio-page.embedded .studio-error {
  padding: 6px 9px;
  font-size: 11px;
}
</style>
