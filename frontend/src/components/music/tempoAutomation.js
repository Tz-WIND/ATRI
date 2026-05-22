export function effectiveTempoAtBeat(project, beat) {
  const safeBeat = Math.max(0, Number(beat || 0))
  let tempo = normalizeTempo(project?.tempo)
  for (const point of automationPointsForTarget(project, 'tempo_bpm')) {
    if (point.beat > safeBeat) break
    tempo = normalizeTempo(point.value, tempo)
  }
  return tempo
}

export function beatsToSeconds(project, beat) {
  const targetBeat = Math.max(0, Number(beat || 0))
  let cursorBeat = 0
  let cursorSeconds = 0
  let tempo = normalizeTempo(project?.tempo)
  for (const point of automationPointsForTarget(project, 'tempo_bpm')) {
    if (point.beat > targetBeat) break
    cursorSeconds += beatsDurationToSeconds(point.beat - cursorBeat, tempo)
    cursorBeat = point.beat
    tempo = normalizeTempo(point.value, tempo)
  }
  return cursorSeconds + beatsDurationToSeconds(targetBeat - cursorBeat, tempo)
}

export function secondsToBeats(project, seconds) {
  let remainingSeconds = Math.max(0, Number(seconds || 0))
  let cursorBeat = 0
  let tempo = normalizeTempo(project?.tempo)
  for (const point of automationPointsForTarget(project, 'tempo_bpm')) {
    const segmentBeats = Math.max(0, point.beat - cursorBeat)
    const segmentSeconds = beatsDurationToSeconds(segmentBeats, tempo)
    if (remainingSeconds <= segmentSeconds) {
      return cursorBeat + secondsDurationToBeats(remainingSeconds, tempo)
    }
    remainingSeconds -= segmentSeconds
    cursorBeat = point.beat
    tempo = normalizeTempo(point.value, tempo)
  }
  return cursorBeat + secondsDurationToBeats(remainingSeconds, tempo)
}

function automationPointsForTarget(project, kind) {
  const points = []
  for (const track of project?.tracks || []) {
    if (track?.type !== 'automation' || track?.mute) continue
    if (track?.target?.kind !== kind) continue
    for (const point of track?.automation?.points || []) {
      const beat = Math.max(0, Number(point?.beat || 0))
      const value = Number(point?.value)
      if (!Number.isFinite(beat) || !Number.isFinite(value)) continue
      points.push({ beat, value })
    }
  }
  return points.sort((a, b) => a.beat - b.beat)
}

function beatsDurationToSeconds(beats, tempo) {
  return Math.max(0, Number(beats || 0)) * 60 / normalizeTempo(tempo)
}

function secondsDurationToBeats(seconds, tempo) {
  return Math.max(0, Number(seconds || 0)) * normalizeTempo(tempo) / 60
}

function normalizeTempo(value, fallback = 120) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.max(1, parsed)
}
