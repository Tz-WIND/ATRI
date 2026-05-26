import { computed, ref, shallowRef } from 'vue'
import { secondsToBeats } from '@/components/music/tempoAutomation.js'
import { useApi } from './useApi.js'

const api = useApi()

const project = shallowRef(null)
const host = ref({ running: false, sample_rate: 48000, buffer_size: 256, binary_path: '' })
const engine = ref(null)
const activeTrackId = ref(1)
const loading = ref(false)
const syncing = ref(false)
const exporting = ref(false)
const exportError = ref('')
const hostError = ref('')
const audioConnected = ref(false)
const audioReady = ref(false)
const hostStreamingEnabled = ref(false)
const pcmStreaming = ref(false)
const playing = ref(false)
const positionSeconds = ref(0)
const plugins = ref({ vst3: [], vst2: [], priority: ['vst3', 'vst2'] })
const pluginsLoading = ref(false)
const editorWindows = ref({})
const pluginParameters = ref({})

let playerNode = null
let audioContext = null
let playerNodeReady = null
let audioOutputUnavailable = false
let audioWs = null
let commandWs = null
let commandWsReady = null
let commandSeq = 0
let pendingAudioHeader = null
let reconnectTimer = null
let pcmStreamingTimer = null
let statusTimer = null
const pendingCommandRequests = new Map()

const tracks = computed(() => project.value?.tracks || [])
const activeTrack = computed(() => (
  tracks.value.find(track => track.id === activeTrackId.value) || tracks.value[0] || null
))
const positionBeats = computed(() => secondsToBeats(project.value, positionSeconds.value))
const totalNotes = computed(() => tracks.value.reduce((sum, track) => sum + track.notes.length, 0))
const learnedAutomationParameters = computed(() => (
  Array.isArray(project.value?.automation_learned_parameters)
    ? project.value.automation_learned_parameters
    : []
))

function setProject(nextProject) {
  if (!nextProject) return
  project.value = nextProject
  if (!tracks.value.some(track => track.id === activeTrackId.value) && tracks.value.length) {
    activeTrackId.value = tracks.value[0].id
  }
}

async function loadProject() {
  loading.value = true
  hostError.value = ''
  try {
    const res = await api.studioProject()
    setProject(res.project)
    if (res.host) host.value = res.host
    if (host.value.running) startStatusPolling()
  } catch (err) {
    hostError.value = err.message || 'Failed to load project'
  } finally {
    loading.value = false
  }
}

async function saveProject(nextProject, options = {}) {
  loading.value = true
  hostError.value = ''
  try {
    const res = await api.saveStudioProject(nextProject, { broadcast: true, ...options })
    if (res.project) setProject(res.project)
    if (res.sync?.host_running) {
      host.value = { ...host.value, running: true }
      startStatusPolling()
    }
    return res
  } catch (err) {
    hostError.value = err.message || 'Failed to save project'
    return null
  } finally {
    loading.value = false
  }
}

function slotIdFromSlotIndex(slotIndex) {
  const index = Number(slotIndex ?? 0)
  if (!Number.isFinite(index) || index <= 0) return 'instrument'
  return `insert_${Math.trunc(index)}`
}

function projectTrackIdFromHostTrackId(hostTrackId) {
  const id = Number(hostTrackId)
  if (!Number.isFinite(id)) return null
  const mapped = tracks.value.find(track => Number(track.host_track_id) === id)
  if (mapped) return mapped.id
  return tracks.value.find(track => Number(track.id) === id)?.id ?? id
}

function editorTitleFor(trackId, slotId) {
  const key = `${trackId}:${slotId}`
  const current = editorWindows.value?.[key]?.title
  if (current) return current

  const track = tracks.value.find(item => Number(item.id) === Number(trackId))
  const slot = (track?.plugin_slots || []).find(item => item?.id === slotId)
  const name = slot?.name || (slotId === 'instrument' ? track?.instrument : '')
  return name ? `${name} - ATRI` : 'Plugin Editor'
}

function syncEditorWindowsFromEngine(engineStatus) {
  if (!Array.isArray(engineStatus?.editor_windows)) return

  const next = {}
  for (const windowStatus of engineStatus.editor_windows) {
    const trackId = projectTrackIdFromHostTrackId(windowStatus?.track_id)
    if (trackId == null) continue
    const slotId = slotIdFromSlotIndex(windowStatus?.slot_index)
    next[`${trackId}:${slotId}`] = {
      open: true,
      title: editorTitleFor(trackId, slotId),
    }
  }
  editorWindows.value = next
}

async function refreshHostStatus() {
  try {
    const res = await api.hostStatus()
    if (res.host) host.value = res.host
    if (res.engine) {
      engine.value = res.engine
      hostStreamingEnabled.value = res.engine.streaming_enabled === true
      playing.value = res.engine.transport === 'playing'
      positionSeconds.value = Number(res.engine.position || 0)
      syncEditorWindowsFromEngine(res.engine)
    } else if (!host.value.running) {
      playing.value = false
      hostStreamingEnabled.value = false
    }
    if (!host.value.running) {
      editorWindows.value = {}
      hostStreamingEnabled.value = false
    }
  } catch {}
}

async function syncProject(options = {}) {
  syncing.value = true
  hostError.value = ''
  try {
    const res = await api.studioSync(options)
    if (res.host) host.value = res.host
    if (res.sync?.project) setProject(res.sync.project)
    return res
  } catch (err) {
    hostError.value = err.message || 'Failed to sync project'
    return null
  } finally {
    syncing.value = false
  }
}

async function resetDemo() {
  loading.value = true
  try {
    const res = await api.studioDemo()
    setProject(res.project)
    if (res.sync?.host_running) {
      host.value = { ...host.value, running: true }
      startStatusPolling()
    }
  } finally {
    loading.value = false
  }
}

async function startHostForTransport() {
  let res = null
  try {
    res = await api.hostStart({ sync: true })
  } catch (err) {
    hostError.value = err.message || 'Audio host failed to start'
    throw err
  }
  if (res.project) setProject(res.project)
  if (res.sync?.project) setProject(res.sync.project)
  if (res.host) host.value = res.host
  if (res.sync?.host_running || res.host?.running) {
    host.value = { ...host.value, running: true }
    startStatusPolling()
  }
  return res
}

async function transport(action, payload = {}) {
  hostError.value = ''
  if (action === 'play') {
    await resumePcmPlayer()
  }
  if (!host.value.running) {
    await refreshHostStatus()
  }
  if (!host.value.running && ['play', 'seek'].includes(action)) {
    await startHostForTransport()
  }
  if (!host.value.running) {
    const message = 'Audio host is not running. Restart ATRI or check the audio host binary.'
    hostError.value = message
    throw new Error(message)
  }
  let res = null
  try {
    res = await api.studioTransport(action, payload)
  } catch (err) {
    hostError.value = err.message || `${action} failed`
    throw err
  }
  if (res.host) host.value = res.host
  if (host.value.running) startStatusPolling()
  if (res.response?.type === 'error') {
    const message = res.response.message || `${action} failed`
    hostError.value = message
    throw new Error(message)
  }
  if (action === 'play') playing.value = true
  if (action === 'pause' || action === 'stop') playing.value = false
  if (action === 'stop') positionSeconds.value = 0
  if (action === 'seek') positionSeconds.value = Number(payload.position || 0)
  window.setTimeout(refreshHostStatus, 150)
  return res
}

async function addNote(trackId, note) {
  const res = await api.studioMidiWrite({
    track_id: trackId,
    mode: 'append',
    notes: [note],
  })
  setProject(res.project)
  return res
}

async function writeNotes(payload) {
  const res = await api.studioMidiWrite(payload)
  setProject(res.project)
  return res
}

async function replaceTrackNotes(trackId, notes) {
  const length = Number(project.value?.length_beats || 16)
  return writeNotes({
    track_id: trackId,
    start: 0,
    end: Math.max(length, ...notes.map(note => Number(note.start || 0) + Number(note.duration || 0))),
    mode: 'replace',
    notes,
  })
}

async function updateTrack(trackId, data) {
  const res = await api.studioUpdateTrack(trackId, data)
  setProject(res.project)
  return res
}

async function createTrack(name = 'Instrument', options = {}) {
  const res = await api.studioCreateTrack(name, options)
  setProject(res.project)
  if (res.track?.id) activeTrackId.value = res.track.id
  return res
}

async function importAudioFile(file, metadata = {}) {
  loading.value = true
  hostError.value = ''
  try {
    const res = await api.studioAudioImport(file, metadata)
    if (res.project) setProject(res.project)
    if (res.track?.id) activeTrackId.value = res.track.id
    return res
  } catch (err) {
    hostError.value = err.message || 'Failed to import audio'
    throw err
  } finally {
    loading.value = false
  }
}

async function exportAudio(payload) {
  exporting.value = true
  exportError.value = ''
  hostError.value = ''
  try {
    const res = await api.studioExportAudio(payload)
    if (res.host) host.value = res.host
    if (res.sync?.project) setProject(res.sync.project)
    return res
  } catch (err) {
    exportError.value = err.message || 'Failed to export audio'
    hostError.value = exportError.value
    throw err
  } finally {
    exporting.value = false
  }
}

async function deleteTrack(trackId) {
  const res = await api.studioDeleteTrack(trackId)
  if (res.project) setProject(res.project)
  return res
}

async function loadPlugins(options = null) {
  pluginsLoading.value = true
  hostError.value = ''
  try {
    const res = await api.studioPlugins(options)
    plugins.value = {
      vst3: Array.isArray(res.plugins?.vst3) ? res.plugins.vst3 : [],
      vst2: Array.isArray(res.plugins?.vst2) ? res.plugins.vst2 : [],
      priority: Array.isArray(res.plugins?.priority) ? res.plugins.priority : ['vst3', 'vst2'],
    }
    if (res.host) host.value = res.host
    return plugins.value
  } catch (err) {
    hostError.value = err.message || 'Failed to scan plugins'
    return plugins.value
  } finally {
    pluginsLoading.value = false
  }
}

async function setTrackPlugin(trackId, plugin, slotId = 'instrument') {
  const res = await api.studioSetTrackPlugin(trackId, plugin, slotId)
  if (res.project) setProject(res.project)
  if (res.host) host.value = res.host
  if (res.load?.type === 'error') {
    hostError.value = res.load.message || 'Plugin load failed'
  }
  return res
}

async function openPluginEditor(trackId, slotId = 'instrument') {
  hostError.value = ''
  if (!host.value.running) {
    await refreshHostStatus()
  }
  if (!host.value.running) {
    const message = 'Audio host is not running. Restart ATRI or check the audio host binary.'
    hostError.value = message
    throw new Error(message)
  }

  try {
    const result = await sendStudioCommand('open_plugin_editor', {
      track_id: trackId,
      slot_id: slotId,
    })
    if (!result.ok) {
      const message = result.response?.message || result.error || 'Plugin editor failed to open'
      hostError.value = message
      throw new Error(message)
    }
    const editorTrackId = result.project_track_id || trackId
    const editorSlotId = result.slot_id || slotId
    editorWindows.value = {
      ...editorWindows.value,
      [`${editorTrackId}:${editorSlotId}`]: {
        open: true,
        title: result.response?.data?.title || result.plugin?.name || 'Plugin Editor',
      },
    }
    if (result.host) host.value = result.host
    return result
  } catch (err) {
    if (!hostError.value) hostError.value = err.message || 'Plugin editor failed to open'
    throw err
  }
}

function pluginParameterKey(trackId, slotId = 'instrument') {
  return `${trackId}:${slotId}`
}

async function loadPluginParameters(trackId, slotId = 'instrument') {
  hostError.value = ''
  try {
    const res = await api.studioPluginParameters(trackId, slotId)
    pluginParameters.value = {
      ...pluginParameters.value,
      [pluginParameterKey(trackId, slotId)]: Array.isArray(res.parameters) ? res.parameters : [],
    }
    if (res.host) host.value = res.host
    return res
  } catch (err) {
    hostError.value = err.message || 'Failed to load plugin parameters'
    throw err
  }
}

async function setPluginParameter(trackId, slotId, paramIndex, value) {
  const res = await api.studioSetPluginParameter({
    track_id: trackId,
    slot_id: slotId,
    param_index: paramIndex,
    value,
  })
  if (res.host) host.value = res.host
  await loadPluginParameters(trackId, slotId).catch(() => null)
  return res
}

async function createAutomationTrack(target, options = {}) {
  const res = await api.studioAutomationWrite({
    target,
    points: Array.isArray(options.points) ? options.points : [
      { beat: Math.max(0, positionBeats.value), value: options.value ?? 0 },
    ],
    name: options.name || target.label || 'Automation',
    color: options.color,
  })
  if (res.project) setProject(res.project)
  return res
}

async function retargetAutomationTrack(trackId, target) {
  const res = await api.studioAutomationRetarget(trackId, target)
  if (res.project) setProject(res.project)
  return res
}

async function pollCapturedPluginParameters() {
  hostError.value = ''
  try {
    const res = await api.studioCapturedPluginParameters()
    if (res.project) setProject(res.project)
    if (res.host) host.value = res.host
    return res
  } catch (err) {
    hostError.value = err.message || 'Failed to poll captured plugin parameters'
    throw err
  }
}

async function renameLearnedAutomationParameter(id, name) {
  const res = await api.studioRenameLearnedPluginParameter(id, name)
  if (res.project) setProject(res.project)
  return res
}

function selectTrack(trackId) {
  activeTrackId.value = trackId
}

async function ensurePcmPlayer() {
  if (playerNode) return playerNode
  if (playerNodeReady) return playerNodeReady
  if (audioOutputUnavailable) return null

  const AudioContextCtor = window.AudioContext || window.webkitAudioContext
  if (!AudioContextCtor || !window.AudioWorkletNode) {
    audioOutputUnavailable = true
    audioReady.value = false
    hostError.value = 'AudioWorklet output is not supported by this browser'
    return null
  }

  playerNodeReady = (async () => {
    const sampleRate = Number(host.value.sample_rate || 48000)
    const options = Number.isFinite(sampleRate) && sampleRate > 0 ? { sampleRate } : undefined
    audioContext = new AudioContextCtor(options)
    audioContext.onstatechange = () => {
      if (audioContext?.state !== 'running') clearPcmStreaming()
    }
    await audioContext.audioWorklet.addModule(
      new URL('../worklets/pcm-player-worklet.js', import.meta.url)
    )
    playerNode = new AudioWorkletNode(audioContext, 'atri-pcm-player', {
      numberOfOutputs: 1,
      outputChannelCount: [2],
    })
    playerNode.connect(audioContext.destination)
    audioReady.value = true
    return playerNode
  })()

  try {
    return await playerNodeReady
  } catch (err) {
    audioOutputUnavailable = true
    audioReady.value = false
    hostError.value = err?.message || 'Audio output failed to initialize'
    closePcmPlayer()
    return null
  } finally {
    playerNodeReady = null
  }
}

async function resumePcmPlayer() {
  await ensurePcmPlayer()
  if (audioContext?.state === 'suspended') {
    await audioContext.resume().catch(() => null)
  }
}

function clearPcmStreaming() {
  clearTimeout(pcmStreamingTimer)
  pcmStreamingTimer = null
  pcmStreaming.value = false
}

function markPcmStreaming() {
  pcmStreaming.value = true
  clearTimeout(pcmStreamingTimer)
  pcmStreamingTimer = setTimeout(() => {
    pcmStreaming.value = false
    pcmStreamingTimer = null
  }, 1200)
}

function closePcmPlayer() {
  clearPcmStreaming()
  audioReady.value = false
  playerNodeReady = null
  if (playerNode) {
    try {
      playerNode.disconnect()
    } catch {}
  }
  playerNode = null
  const context = audioContext
  audioContext = null
  if (context && context.state !== 'closed') {
    context.close().catch(() => null)
  }
}

function connectAudioStream() {
  if (audioWs || !host.value.running) return
  clearTimeout(reconnectTimer)
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  audioWs = new WebSocket(`${protocol}//${location.host}/ws/audio`)
  audioWs.binaryType = 'arraybuffer'
  audioWs.onopen = () => {
    audioConnected.value = true
  }
  audioWs.onmessage = async (event) => {
    if (typeof event.data === 'string') {
      try {
        pendingAudioHeader = JSON.parse(event.data)
      } catch {
        pendingAudioHeader = null
      }
      return
    }
    const buffer = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data
    const header = pendingAudioHeader || {}
    pendingAudioHeader = null
    await ensurePcmPlayer()
    if (!playerNode) return
    playerNode.port.postMessage(
      {
        type: 'samples',
        buffer,
        channels: Number(header.channels || 2),
        sampleRate: Number(header.sample_rate || host.value.sample_rate),
      },
      [buffer]
    )
    if (audioContext?.state === 'running') markPcmStreaming()
  }
  audioWs.onclose = () => {
    audioConnected.value = false
    clearPcmStreaming()
    audioWs = null
    if (host.value.running) {
      reconnectTimer = setTimeout(connectAudioStream, 1200)
    }
  }
  audioWs.onerror = () => {
    if (audioWs) audioWs.close()
  }
}

function disconnectAudioStream() {
  clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (audioWs) {
    audioWs.onclose = null
    audioWs.close()
    audioWs = null
  }
  audioConnected.value = false
  clearPcmStreaming()
  closePcmPlayer()
  pendingAudioHeader = null
}

function ensureCommandWs() {
  if (commandWs?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (commandWsReady) return commandWsReady

  commandWsReady = new Promise((resolve, reject) => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    commandWs = new WebSocket(`${protocol}//${location.host}/ws`)

    commandWs.onopen = () => {
      commandWsReady = null
      resolve()
    }
    commandWs.onmessage = handleCommandMessage
    commandWs.onclose = () => {
      commandWs = null
      commandWsReady = null
      for (const [, pending] of pendingCommandRequests) {
        clearTimeout(pending.timer)
        pending.reject(new Error('Studio command socket closed'))
      }
      pendingCommandRequests.clear()
    }
    commandWs.onerror = () => {
      if (commandWs) commandWs.close()
      reject(new Error('Studio command socket failed'))
    }
  })

  return commandWsReady
}

async function sendStudioCommand(cmd, payload = {}) {
  try {
    await ensureCommandWs()
  } catch {
    if (cmd === 'open_plugin_editor') {
      return api.studioOpenPluginEditor(payload.track_id, payload.slot_id)
    }
    throw new Error('Studio command socket failed')
  }
  if (!commandWs || commandWs.readyState !== WebSocket.OPEN) {
    return api.studioOpenPluginEditor(payload.track_id, payload.slot_id)
  }

  commandSeq += 1
  const requestId = `studio_${Date.now()}_${commandSeq}`
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingCommandRequests.delete(requestId)
      reject(new Error('Timed out waiting for studio command response'))
    }, 6000)
    pendingCommandRequests.set(requestId, { resolve, reject, timer })
    commandWs.send(JSON.stringify({
      type: 'studio_command',
      cmd,
      request_id: requestId,
      ...payload,
    }))
  })
}

function handleCommandMessage(event) {
  let msg = null
  try {
    msg = JSON.parse(event.data)
  } catch {
    return
  }
  if (msg?.type !== 'studio_command_result') return
  const pending = pendingCommandRequests.get(msg.request_id)
  if (!pending) return
  clearTimeout(pending.timer)
  pendingCommandRequests.delete(msg.request_id)
  pending.resolve(msg)
}

function startStatusPolling() {
  if (statusTimer) return
  statusTimer = setInterval(refreshHostStatus, 1000)
}

function handleProjectBroadcast(msg) {
  if (msg?.project) {
    setProject(msg.project)
  }
}

export function useDawHost() {
  return {
    project,
    host,
    engine,
    tracks,
    activeTrack,
    activeTrackId,
    loading,
    syncing,
    exporting,
    exportError,
    hostError,
    audioConnected,
    audioReady,
    hostStreamingEnabled,
    pcmStreaming,
    playing,
    positionSeconds,
    positionBeats,
    totalNotes,
    plugins,
    pluginsLoading,
    editorWindows,
    pluginParameters,
    learnedAutomationParameters,
    loadProject,
    saveProject,
    refreshHostStatus,
    syncProject,
    resetDemo,
    transport,
    addNote,
    writeNotes,
    replaceTrackNotes,
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
    pollCapturedPluginParameters,
    renameLearnedAutomationParameter,
    selectTrack,
    connectAudioStream,
    disconnectAudioStream,
    handleProjectBroadcast,
  }
}
