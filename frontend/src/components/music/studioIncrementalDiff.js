function byId(items) {
  return new Map((items || []).filter(item => item?.id).map(item => [String(item.id), item]))
}

function numbersEqual(a, b) {
  return Math.abs(Number(a || 0) - Number(b || 0)) <= 0.000001
}

function notePayload(note) {
  return {
    id: String(note.id),
    pitch: Number(note.pitch),
    local_start: Number(note.start || 0),
    duration: Number(note.duration || 0.25),
    velocity: Number(note.velocity || 96),
  }
}

function notesEqual(a, b) {
  return Number(a?.pitch) === Number(b?.pitch)
    && numbersEqual(a?.start, b?.start)
    && numbersEqual(a?.duration, b?.duration)
    && Number(a?.velocity) === Number(b?.velocity)
}

function eventPayload(event) {
  const payload = { ...(event || {}) }
  payload.id = String(payload.id)
  payload.local_start = Number(payload.start || 0)
  delete payload.start
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== undefined && value !== null)
  )
}

function eventsEqual(a, b) {
  return JSON.stringify(eventPayload(a)) === JSON.stringify(eventPayload(b))
}

function pointsEqual(a, b) {
  return JSON.stringify(a || []) === JSON.stringify(b || [])
}

function clipPayload(clip) {
  return {
    ...(clip || {}),
    notes: (clip?.notes || []).map(note => ({ ...note })),
    events: (clip?.events || []).map(event => ({ ...event })),
  }
}

function clipsEqual(a, b) {
  return JSON.stringify(clipPayload(a)) === JSON.stringify(clipPayload(b))
}

export function buildMidiNoteDiffOperations(previousNotes, nextNotes, clipId) {
  const previousById = byId(previousNotes)
  const nextById = byId(nextNotes)
  const operations = []

  for (const previous of previousById.values()) {
    if (!nextById.has(String(previous.id))) {
      operations.push({ op: 'delete_note', clip_id: clipId, id: String(previous.id) })
    }
  }

  for (const next of nextById.values()) {
    const previous = previousById.get(String(next.id))
    if (!previous) continue
    if (notesEqual(previous, next)) continue
    const payload = notePayload(next)
    operations.push({
      op: 'update_note',
      clip_id: clipId,
      id: payload.id,
      pitch: payload.pitch,
      local_start: payload.local_start,
      duration: payload.duration,
      velocity: payload.velocity,
    })
  }

  for (const next of nextById.values()) {
    if (!previousById.has(String(next.id))) {
      operations.push({ op: 'add_note', clip_id: clipId, note: notePayload(next) })
    }
  }

  return operations
}

export function buildMidiEventDiffOperations(previousEvents, nextEvents, clipId) {
  const previousById = byId(previousEvents)
  const nextById = byId(nextEvents)
  const operations = []

  for (const previous of previousById.values()) {
    if (!nextById.has(String(previous.id))) {
      operations.push({ op: 'delete_event', clip_id: clipId, id: String(previous.id) })
    }
  }

  for (const next of nextById.values()) {
    const previous = previousById.get(String(next.id))
    if (!previous) continue
    if (eventsEqual(previous, next)) continue
    operations.push({
      op: 'update_event',
      clip_id: clipId,
      id: String(next.id),
      event: eventPayload(next),
    })
  }

  for (const next of nextById.values()) {
    if (!previousById.has(String(next.id))) {
      operations.push({
        op: 'add_event',
        clip_id: clipId,
        event: eventPayload(next),
      })
    }
  }

  return operations
}

export function buildAutomationReplaceRangeOperations(previousPoints, nextPoints) {
  if (pointsEqual(previousPoints, nextPoints)) return []
  const end = Math.max(
    0,
    ...(previousPoints || []).map(point => Number(point?.beat || 0)),
    ...(nextPoints || []).map(point => Number(point?.beat || 0))
  )
  return [{
    op: 'replace_range',
    start: 0,
    end,
    points: nextPoints || [],
  }]
}

export function buildClipDiffOperations(previousRecords, nextRecords) {
  const previousById = byId((previousRecords || []).map(record => ({
    id: record?.clip?.id,
    record,
  })))
  const nextById = byId((nextRecords || []).map(record => ({
    id: record?.clip?.id,
    record,
  })))
  const operations = []

  for (const previous of previousById.values()) {
    const clipId = previous.record.clip.id
    if (!nextById.has(String(clipId))) {
      operations.push({ op: 'delete_clip', clip_id: String(clipId) })
    }
  }

  for (const next of nextById.values()) {
    const clipId = next.record.clip.id
    const previous = previousById.get(String(clipId))
    if (!previous) continue
    const movedTracks = String(previous.record.trackId) !== String(next.record.trackId)
    if (!movedTracks && clipsEqual(previous.record.clip, next.record.clip)) continue
    operations.push({
      op: 'update_clip',
      clip_id: String(clipId),
      track_id: next.record.trackId,
      clip: clipPayload(next.record.clip),
    })
  }

  for (const next of nextById.values()) {
    const clipId = next.record.clip.id
    if (!previousById.has(String(clipId))) {
      operations.push({
        op: 'add_clip',
        track_id: next.record.trackId,
        clip: clipPayload(next.record.clip),
      })
    }
  }

  return operations
}
