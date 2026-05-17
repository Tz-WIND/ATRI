export const PIANO_QUANTIZE_OPTIONS = [
  { id: 'off', label: '关闭', step: null },
  { id: '1/4', label: '1/4', step: 1 },
  { id: '1/8', label: '1/8', step: 0.5 },
  { id: '1/16', label: '1/16', step: 0.25 },
  { id: '1/32', label: '1/32', step: 0.125 },
  { id: '1/64', label: '1/64', step: 0.0625 },
]

const quantizeSteps = new Map(PIANO_QUANTIZE_OPTIONS.map(option => [option.id, option.step]))

export function quantizeStepFromId(id) {
  return quantizeSteps.has(id) ? quantizeSteps.get(id) : quantizeSteps.get('1/16')
}

export function snapBeatToGrid(value, step) {
  const beat = Number(value || 0)
  if (!Number.isFinite(beat)) return 0
  if (!Number.isFinite(step) || Number(step) <= 0) return roundBeat(beat)
  return roundBeat(Math.round(beat / step) * step)
}

export function quantizedBeatsBetween(startBeat, endBeat, step) {
  const start = Number(startBeat || 0)
  const end = Number(endBeat || 0)
  if (!Number.isFinite(step) || Number(step) <= 0) return [roundBeat(end)]

  const safeStep = Number(step)
  const min = Math.min(start, end)
  const max = Math.max(start, end)
  const beats = []
  const first = Math.ceil((min - 0.000001) / safeStep) * safeStep

  for (let beat = first; beat <= max + 0.000001; beat += safeStep) {
    beats.push(roundBeat(beat))
  }

  const snappedEnd = snapBeatToGrid(end, safeStep)
  if (!beats.includes(snappedEnd)) beats.push(snappedEnd)
  return [...new Set(beats)].sort((a, b) => start <= end ? a - b : b - a)
}

export function interpolateControllerValue(startBeat, startValue, endBeat, endValue, beat) {
  const distance = Number(endBeat || 0) - Number(startBeat || 0)
  if (Math.abs(distance) < 0.000001) return Math.round(Number(endValue || 0))
  const unit = (Number(beat || 0) - Number(startBeat || 0)) / distance
  return Math.round(Number(startValue || 0) + (Number(endValue || 0) - Number(startValue || 0)) * unit)
}

function roundBeat(value) {
  return Math.round(Number(value || 0) * 1000000) / 1000000
}
