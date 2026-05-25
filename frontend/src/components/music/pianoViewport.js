export const DEFAULT_PIANO_FOCUS_PITCH = 60

export function pianoFocusPitch(notes = [], selectedNoteIds = new Set()) {
  const selectedIds = selectedNoteIds instanceof Set
    ? selectedNoteIds
    : new Set(selectedNoteIds || [])
  const selectedNotes = notes.filter(note => selectedIds.has(note.id))
  const candidates = selectedNotes.length ? selectedNotes : notes
  const pitches = candidates
    .map(note => Number(note.pitch))
    .filter(Number.isFinite)

  if (!pitches.length) return DEFAULT_PIANO_FOCUS_PITCH
  return (Math.min(...pitches) + Math.max(...pitches)) / 2
}

export function pianoScrollTopForNotes({
  notes = [],
  selectedNoteIds = new Set(),
  minPitch,
  maxPitch,
  rowHeight,
  noteTop,
  clientHeight,
  scrollHeight,
}) {
  const focusPitch = clamp(pianoFocusPitch(notes, selectedNoteIds), minPitch, maxPitch)
  const rowCenterY = noteTop + (maxPitch - focusPitch) * rowHeight + rowHeight / 2
  const maxScrollTop = Math.max(0, Number(scrollHeight || 0) - Number(clientHeight || 0))
  return Math.round(clamp(rowCenterY - Number(clientHeight || 0) / 2, 0, maxScrollTop))
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}
