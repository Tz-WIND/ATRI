<template>
  <section
    v-if="loading || artifact || error || bridgeExportError"
    class="midi-artifact"
  >
    <header class="midi-artifact-head">
      <div class="midi-title-stack">
        <span class="midi-kicker">MIDI</span>
        <strong>{{ trackLabel }}</strong>
      </div>
      <span class="midi-range">{{ rangeLabel }}</span>
      <span
        v-if="bridgeStatusLabel"
        class="midi-bridge-status"
      >{{ bridgeStatusLabel }}</span>
      <div class="midi-actions">
        <button
          class="midi-action"
          type="button"
          :disabled="!artifact || previewing"
          @click="playPreview"
        >
          {{ playing ? 'Stop' : previewing ? 'Rendering' : 'Play' }}
        </button>
        <a
          v-if="midiExport?.download_url"
          class="midi-action link"
          :href="midiExport.download_url"
          :download="midiExport.filename || ''"
          draggable="true"
          @dragstart="startMidiDrag"
        >
          MIDI
        </a>
        <button
          v-else
          class="midi-action"
          type="button"
          :disabled="!artifact || exporting"
          @click="exportMidi"
        >
          {{ exporting ? 'Exporting' : 'MIDI' }}
        </button>
      </div>
    </header>

    <div class="midi-canvas-shell">
      <canvas
        ref="canvasRef"
        class="midi-canvas"
        aria-hidden="true"
      />
    </div>

    <div
      v-if="error || bridgeExportError"
      class="midi-error"
    >
      {{ error || bridgeExportError }}
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useApi } from '@/composables/useApi.js'
import { useDawHost } from '@/composables/useDawHost.js'
import {
  bridgeAutoExportKeyForArtifact,
  bridgeInstanceIdFromLocation,
  buildMidiArtifactPreview,
  exportPayloadForMidiArtifact,
  isDawAgentSurfaceLocation,
} from './midiArtifact.js'

const props = defineProps({
  toolData: { type: Object, required: true },
})

const api = useApi()
const { project, projectRevision, loadProject: loadHostProject } = useDawHost()
const loading = ref(false)
const error = ref('')
const previewing = ref(false)
const playing = ref(false)
const exporting = ref(false)
const autoExporting = ref(false)
const bridgeExportError = ref('')
const lastAutoExportKey = ref('')
const midiExport = ref(null)
const canvasRef = ref(null)
let previewAudio = null

const artifact = computed(() => buildMidiArtifactPreview(props.toolData, project.value))
const trackLabel = computed(() => artifact.value?.track?.name || 'MIDI region')
const rangeLabel = computed(() => {
  const range = artifact.value?.range
  if (!range) return loading.value ? 'Loading' : ''
  const beats = Math.max(0, range.end - range.start)
  return `${formatBeat(range.start)}-${formatBeat(range.end)} · ${beats.toFixed(2)} beat`
})
const bridgeStatusLabel = computed(() => {
  if (!isDawAgentSurfaceLocation() || !bridgeInstanceIdFromLocation()) return ''
  if (autoExporting.value) return 'Sending to bridge'
  if (bridgeExportError.value) return 'Bridge export failed'
  if (midiExport.value?.path) return 'Bridge ready'
  return ''
})

onMounted(() => {
  ensureHostProject()
})

onBeforeUnmount(stopPreview)

watch(
  () => `${props.toolData?.tool}:${JSON.stringify(props.toolData?.args || {})}`,
  () => {
    midiExport.value = null
    bridgeExportError.value = ''
    lastAutoExportKey.value = ''
    ensureHostProject()
  }
)

watch(
  () => props.toolData?.status,
  async (status) => {
    if (status === 'success') {
      await refreshHostProject()
      await autoExportBridgeMidi()
    }
  }
)

watch(projectRevision, () => {
  drawArtifact()
})

watch(artifact, () => {
  drawArtifact()
  autoExportBridgeMidi()
}, { immediate: true })

async function ensureHostProject() {
  if (project.value) return
  await refreshHostProject()
}

async function refreshHostProject() {
  loading.value = true
  error.value = ''
  try {
    await loadHostProject()
  } catch (err) {
    error.value = err.message || 'MIDI preview unavailable'
  } finally {
    loading.value = false
    drawArtifact()
  }
}

async function playPreview() {
  if (playing.value) {
    stopPreview()
    return
  }
  if (!artifact.value) return

  previewing.value = true
  error.value = ''
  try {
    const payload = exportPayloadForMidiArtifact(artifact.value, 'wav')
    const res = await api.studioExportAudio(payload)
    const url = absoluteUrl(res.export?.download_url)
    if (!url) throw new Error('preview render did not return audio')
    stopPreview()
    previewAudio = new Audio(url)
    previewAudio.onended = () => {
      playing.value = false
      previewAudio = null
    }
    previewAudio.onerror = () => {
      playing.value = false
      error.value = 'Preview playback failed'
      previewAudio = null
    }
    await previewAudio.play()
    playing.value = true
  } catch (err) {
    error.value = err.message || 'Preview render failed'
  } finally {
    previewing.value = false
  }
}

async function autoExportBridgeMidi() {
  const instanceId = bridgeInstanceIdFromLocation()
  if (!isDawAgentSurfaceLocation() || !instanceId) return
  if (props.toolData?.status && props.toolData.status !== 'success') return
  if (!artifact.value || autoExporting.value) return

  const key = bridgeAutoExportKeyForArtifact(artifact.value, props.toolData)
  if (!key || key === lastAutoExportKey.value) return

  const payload = exportPayloadForMidiArtifact(artifact.value, 'midi', { instanceId })
  if (!payload) return

  autoExporting.value = true
  bridgeExportError.value = ''
  try {
    const res = await api.studioExportAudio(payload)
    midiExport.value = res.export || null
    lastAutoExportKey.value = key
  } catch (err) {
    bridgeExportError.value = err.message || 'MIDI bridge export failed'
  } finally {
    autoExporting.value = false
  }
}

async function exportMidi() {
  if (!artifact.value) return
  exporting.value = true
  error.value = ''
  try {
    const payload = exportPayloadForMidiArtifact(artifact.value, 'midi', {
      instanceId: bridgeInstanceIdFromLocation(),
    })
    const res = await api.studioExportAudio(payload)
    midiExport.value = res.export || null
    lastAutoExportKey.value = bridgeAutoExportKeyForArtifact(artifact.value, props.toolData)
  } catch (err) {
    error.value = err.message || 'MIDI export failed'
  } finally {
    exporting.value = false
  }
}

function startMidiDrag(event) {
  const item = midiExport.value
  const url = absoluteUrl(item?.download_url)
  if (!url || !event.dataTransfer) return
  const filename = item.filename || 'atri-midi-region.mid'
  event.dataTransfer.effectAllowed = 'copy'
  event.dataTransfer.setData('text/uri-list', url)
  event.dataTransfer.setData('text/plain', url)
  event.dataTransfer.setData('DownloadURL', `audio/midi:${filename}:${url}`)
}

function stopPreview() {
  if (previewAudio) {
    previewAudio.pause()
    previewAudio = null
  }
  playing.value = false
}

function drawArtifact() {
  nextTick(() => {
    const canvas = canvasRef.value
    const view = artifact.value
    if (!canvas || !view) return
    const cssWidth = Math.max(360, Math.round(canvas.getBoundingClientRect().width || 640))
    const cssHeight = 164
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(cssWidth * dpr)
    canvas.height = Math.round(cssHeight * dpr)
    canvas.style.height = `${cssHeight}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    paintPianoRoll(ctx, view, cssWidth, cssHeight)
  })
}

function paintPianoRoll(ctx, view, width, height) {
  const keyW = 46
  const rulerH = 24
  const bodyW = width - keyW - 10
  const bodyH = height - rulerH - 8
  const low = view.pitchRange.low
  const high = view.pitchRange.high
  const pitchCount = high - low + 1
  const rowH = bodyH / pitchCount
  const range = view.range
  const duration = Math.max(0.25, range.end - range.start)

  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#111317'
  ctx.fillRect(0, 0, width, height)

  for (let pitch = low; pitch <= high; pitch += 1) {
    const y = rulerH + (high - pitch) * rowH
    const isBlack = [1, 3, 6, 8, 10].includes(pitch % 12)
    ctx.fillStyle = isBlack ? '#171a20' : '#20242b'
    ctx.fillRect(0, y, keyW, Math.ceil(rowH))
    ctx.fillStyle = isBlack ? '#2a2f38' : '#3a414c'
    ctx.fillRect(keyW, y, bodyW, 1)
    if (pitch % 12 === 0) {
      ctx.fillStyle = '#8b929d'
      ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
      ctx.fillText(`C${Math.floor(pitch / 12) - 1}`, 8, y + Math.min(rowH - 2, 12))
    }
  }

  ctx.fillStyle = '#16191f'
  ctx.fillRect(keyW, 0, bodyW, rulerH)
  ctx.strokeStyle = 'rgba(255,255,255,0.12)'
  ctx.beginPath()
  for (let beat = Math.ceil(range.start); beat <= range.end; beat += 1) {
    const x = keyW + ((beat - range.start) / duration) * bodyW
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height - 8)
    ctx.fillStyle = '#9aa2ad'
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
    ctx.fillText(String(beat), x + 4, 15)
  }
  ctx.stroke()

  for (const note of view.notes) {
    const noteStart = Math.max(note.start, range.start)
    const noteEnd = Math.min(note.start + note.duration, range.end)
    const x = keyW + ((noteStart - range.start) / duration) * bodyW
    const w = Math.max(4, ((noteEnd - noteStart) / duration) * bodyW)
    const y = rulerH + (high - note.pitch) * rowH + 2
    const h = Math.max(4, rowH - 4)
    ctx.fillStyle = 'rgba(86, 150, 255, 0.86)'
    ctx.fillRect(x, y, w, h)
    ctx.fillStyle = 'rgba(255,255,255,0.18)'
    ctx.fillRect(x, y, Math.min(w, 2), h)
  }

  ctx.strokeStyle = 'rgba(255,255,255,0.16)'
  ctx.strokeRect(0.5, 0.5, width - 1, height - 1)
}

function formatBeat(value) {
  return Number(value || 0).toFixed(2).replace(/\.00$/, '')
}

function absoluteUrl(url) {
  if (!url) return ''
  return new URL(url, window.location.href).toString()
}
</script>

<style scoped>
.midi-artifact {
  width: min(760px, calc(100% - 35px));
  margin: 7px 0 10px 35px;
  padding: 10px;
  border: 1px solid rgba(229, 236, 245, 0.09);
  border-radius: 8px;
  background: rgba(18, 20, 25, 0.78);
  color: var(--t2);
}

.midi-artifact-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.midi-title-stack {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.midi-kicker,
.midi-range {
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.midi-bridge-status {
  color: var(--acc2);
  font-family: var(--mono);
  font-size: 11px;
  white-space: nowrap;
}

.midi-title-stack strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t1);
  font-size: 13px;
  font-weight: 650;
}

.midi-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.midi-action {
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 9px;
  border: 1px solid rgba(229, 236, 245, 0.11);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--t2);
  font: 600 11px/1 var(--sans);
  text-decoration: none;
  cursor: pointer;
}

.midi-action:hover:not(:disabled) {
  color: var(--t1);
  border-color: rgba(125, 168, 232, 0.34);
}

.midi-action:disabled {
  opacity: 0.5;
  cursor: default;
}

.midi-canvas-shell {
  width: 100%;
  overflow: hidden;
  border-radius: 6px;
  background: #111317;
}

.midi-canvas {
  width: 100%;
  display: block;
}

.midi-error {
  margin-top: 8px;
  color: var(--red);
  font-size: 12px;
}

@media (max-width: 620px) {
  .midi-artifact {
    width: 100%;
    margin-left: 0;
  }

  .midi-artifact-head {
    grid-template-columns: minmax(0, 1fr);
  }

  .midi-actions {
    justify-content: flex-start;
  }
}
</style>
