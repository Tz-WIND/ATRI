import { beatsToSeconds } from '../music/tempoAutomation.js'

const MIDI_ARTIFACT_TOOLS = new Set(['midi_write', 'midi_diff', 'midi_batch_edit'])

export function bridgeInstanceIdFromLocation(location = globalThis.window?.location) {
  const search = typeof location?.search === 'string' ? location.search : ''
  return new URLSearchParams(search).get('instance_id') || ''
}

export function isDawAgentSurfaceLocation(location = globalThis.window?.location) {
  const search = typeof location?.search === 'string' ? location.search : ''
  return new URLSearchParams(search).get('surface') === 'daw-agent'
}

export function bridgeAutoExportKeyForArtifact(view, toolData) {
  const trackId = Number(view?.track?.id)
  const start = Number(view?.range?.start)
  const end = Number(view?.range?.end)
  if (!Number.isFinite(trackId) || !Number.isFinite(start) || !Number.isFinite(end)) {
    return ''
  }
  const tool = String(toolData?.tool || '')
  const args = stableJson(toolData?.args || {})
  return `${tool}:${trackId}:${start}:${end}:${args}`
}

export function isMidiArtifactTool(toolName) {
  return MIDI_ARTIFACT_TOOLS.has(String(toolName || '').trim())
}

export function buildMidiArtifactView(toolData, project) {
  if (!isMidiArtifactTool(toolData?.tool) || !project) return null
  const args = toolData.args || {}
  const track = findTrack(project, artifactTrackId(args))
  if (!track) return null

  const notes = normalizedTrackNotes(track)
  const events = normalizedTrackEvents(track)
  const range = editedRangeForTool(toolData.tool, args, notes, events)
  if (!range) return null

  const visibleNotes = notes.filter(noteOverlapsRange(range))
  const visibleEvents = events.filter(eventInRange(range))
  return {
    project,
    track,
    range,
    notes: visibleNotes,
    events: visibleEvents,
    pitchRange: pitchRangeForNotes(visibleNotes),
  }
}

export function buildMidiArtifactViewFromArgs(toolData, project = null) {
  if (toolData?.tool !== 'midi_write') return null
  const args = toolData.args || {}
  const trackId = artifactTrackId(args)
  if (trackId == null) return null

  const notes = (args.notes || []).map(normalizeNote).filter(Boolean).sort(sortNotes)
  const range = editedRangeForTool('midi_write', args, notes, [])
  if (!range) return null

  const visibleNotes = notes.filter(noteOverlapsRange(range))
  const projectTrack = findTrack(project, trackId)
  const track = projectTrack
    ? { ...projectTrack, id: Number(trackId) }
    : { id: Number(trackId), name: `Track ${trackId}` }

  return {
    project: project || defaultPreviewProject(range),
    track,
    range,
    notes: visibleNotes,
    events: [],
    pitchRange: pitchRangeForNotes(visibleNotes),
  }
}

export function buildMidiArtifactPreview(toolData, project) {
  const fromProject = buildMidiArtifactView(toolData, project)
  const fromArgs = buildMidiArtifactViewFromArgs(toolData, project)
  const mode = String(toolData?.args?.mode || 'replace').trim().toLowerCase()

  if (toolData?.tool === 'midi_write' && mode !== 'append') {
    if (fromArgs) {
      if (fromProject?.track?.name) {
        fromArgs.track = { ...fromArgs.track, name: fromProject.track.name }
      }
      if (fromProject?.project) fromArgs.project = fromProject.project
      return fromArgs
    }
    return fromProject
  }

  return fromProject || fromArgs
}

export function exportPayloadForMidiArtifact(view, format, options = {}) {
  const trackId = Number(view?.track?.id)
  const start = Number(view?.range?.start)
  const end = Number(view?.range?.end)
  if (!Number.isFinite(trackId) || !Number.isFinite(start) || !Number.isFinite(end)) {
    return null
  }

  if (format === 'midi') {
    const payload = {
      target: 'selected_tracks',
      track_ids: [trackId],
      format: 'midi',
      consumer: 'bridge',
      start_beat: start,
      end_beat: end,
    }
    const instanceId = String(options.instanceId || options.instance_id || '').trim()
    if (instanceId) payload.instance_id = instanceId
    return payload
  }

  return {
    target: 'selected_tracks',
    track_ids: [trackId],
    mode: 'mixdown',
    format: 'wav',
    sample_rate: 48000,
    bit_depth: 'i24',
    start: beatsToSeconds(view.project, start),
    end: beatsToSeconds(view.project, end),
  }
}

function editedRangeForTool(toolName, args, notes, events) {
  if (toolName === 'midi_write') {
    return midiWriteRange(args)
  }
  if (toolName === 'midi_batch_edit') {
    return normalizeRange(args.selection?.range)
  }
  return mergeRanges((args.operations || []).map(op => operationRange(op, notes, events)))
}

function midiWriteRange(args) {
  const explicit = normalizeStartEnd(args.start, args.end)
  if (explicit) return explicit
  return mergeRanges((args.notes || []).map(noteRange))
}

function operationRange(op, notes, events) {
  if (!op || typeof op !== 'object') return null
  return mergeRanges([
    normalizeRange(op.range),
    normalizeRange(op.selection?.range),
    normalizeStartEnd(op.start, op.end),
    operationNoteRange(op),
    pointsRange(op.points),
    noteByIdRange(notes, op.id || op.note_id),
    eventByIdRange(events, op.event_id),
  ])
}

function operationNoteRange(op) {
  if (op.note && typeof op.note === 'object') return noteRange(op.note)
  if (!('pitch' in op) && !('duration' in op)) return null
  if (!('start' in op) && !('local_start' in op)) return null
  return noteRange({ ...op, start: op.start ?? op.local_start })
}

function artifactTrackId(args) {
  const selectionIds = args?.selection?.track_ids
  if (args?.track_id != null) return args.track_id
  if (Array.isArray(selectionIds) && selectionIds.length) return selectionIds[0]
  return null
}

function findTrack(project, trackId) {
  const id = Number(trackId)
  if (!Number.isFinite(id)) return null
  return (project.tracks || []).find(track => Number(track?.id) === id) || null
}

function normalizedTrackNotes(track) {
  if (Array.isArray(track.notes) && track.notes.length) {
    return track.notes.map(normalizeNote).filter(Boolean).sort(sortNotes)
  }
  const notes = []
  for (const clip of track.clips || []) {
    if (clip?.type !== 'midi') continue
    const clipStart = finiteNumber(clip.start, 0)
    for (const note of clip.notes || []) {
      const normalized = normalizeNote({ ...note, start: clipStart + finiteNumber(note.start, 0) })
      if (normalized) notes.push(normalized)
    }
  }
  return notes.sort(sortNotes)
}

function normalizedTrackEvents(track) {
  if (Array.isArray(track.midi_events) && track.midi_events.length) {
    return track.midi_events.map(normalizeEvent).filter(Boolean).sort(sortEvents)
  }
  const events = []
  for (const clip of track.clips || []) {
    if (clip?.type !== 'midi') continue
    const clipStart = finiteNumber(clip.start, 0)
    for (const event of clip.events || []) {
      const normalized = normalizeEvent({ ...event, start: clipStart + finiteNumber(event.start, 0) })
      if (normalized) events.push(normalized)
    }
  }
  return events.sort(sortEvents)
}

function normalizeNote(note) {
  if (!note || typeof note !== 'object') return null
  const start = finiteNumber(note.start ?? note.beat, 0)
  const duration = Math.max(0.001, finiteNumber(note.duration, 0.25))
  const pitch = Math.max(0, Math.min(127, Math.round(finiteNumber(note.pitch, 60))))
  return {
    ...note,
    start,
    duration,
    pitch,
    velocity: Math.max(1, Math.min(127, Math.round(finiteNumber(note.velocity, 96)))),
  }
}

function normalizeEvent(event) {
  if (!event || typeof event !== 'object') return null
  return { ...event, start: finiteNumber(event.start ?? event.beat, 0) }
}

function noteRange(note) {
  const normalized = normalizeNote(note)
  if (!normalized) return null
  return { start: normalized.start, end: normalized.start + normalized.duration }
}

function pointsRange(points) {
  if (!Array.isArray(points) || !points.length) return null
  const starts = points.map(point => finiteNumber(point?.start, NaN)).filter(Number.isFinite)
  if (!starts.length) return null
  const start = Math.min(...starts)
  const end = Math.max(...starts)
  return { start, end: end > start ? end : start + 0.25 }
}

function noteByIdRange(notes, id) {
  if (!id) return null
  const note = notes.find(item => String(item.id) === String(id))
  return noteRange(note)
}

function eventByIdRange(events, id) {
  if (!id) return null
  const event = events.find(item => String(item.id) === String(id))
  return event ? { start: event.start, end: event.start + 0.25 } : null
}

function normalizeRange(value) {
  if (!Array.isArray(value) || value.length < 2) return null
  return normalizeStartEnd(value[0], value[1])
}

function normalizeStartEnd(startValue, endValue) {
  const start = finiteNumber(startValue, NaN)
  const end = finiteNumber(endValue, NaN)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
  return { start: Math.max(0, start), end: Math.max(0, end) }
}

function mergeRanges(ranges) {
  const valid = ranges.filter(Boolean)
  if (!valid.length) return null
  return {
    start: Math.min(...valid.map(range => range.start)),
    end: Math.max(...valid.map(range => range.end)),
  }
}

function noteOverlapsRange(range) {
  return note => note.start < range.end && note.start + note.duration > range.start
}

function eventInRange(range) {
  return event => event.start >= range.start && event.start <= range.end
}

function pitchRangeForNotes(notes) {
  if (!notes.length) return { low: 48, high: 72 }
  const pitches = notes.map(note => note.pitch)
  const low = Math.max(0, Math.min(...pitches) - 2)
  const high = Math.min(127, Math.max(...pitches) + 2)
  return { low, high: Math.max(low + 1, high) }
}

function finiteNumber(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function sortNotes(a, b) {
  return a.start - b.start || a.pitch - b.pitch || a.duration - b.duration
}

function sortEvents(a, b) {
  return a.start - b.start
}

function stableJson(value) {
  return JSON.stringify(sortStable(value))
}

function sortStable(value) {
  if (Array.isArray(value)) return value.map(sortStable)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map(key => [key, sortStable(value[key])])
  )
}

function defaultPreviewProject(range) {
  return {
    tempo: 120,
    length_beats: Math.max(Number(range?.end || 0), 16),
  }
}
