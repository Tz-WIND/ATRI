export const VIEWPORT_PAD_PX = 64

export function scrollViewport({
  scrollLeft = 0,
  scrollTop = 0,
  clientWidth = 0,
  clientHeight = 0,
  pad = VIEWPORT_PAD_PX,
} = {}) {
  const left = Number(scrollLeft) || 0
  const top = Number(scrollTop) || 0
  const width = Number(clientWidth) || 0
  const height = Number(clientHeight) || 0
  const padding = Number(pad) || 0
  return {
    xStart: Math.max(0, left - padding),
    xEnd: left + width + padding,
    yStart: Math.max(0, top - padding),
    yEnd: top + height + padding,
  }
}

export function rangesOverlap(startA, endA, startB, endB) {
  return startA < endB && startB < endA
}

export function rectIntersectsViewport(rect, viewport) {
  if (!rect || !viewport) return true
  return rangesOverlap(
    Number(rect.x) || 0,
    (Number(rect.x) || 0) + (Number(rect.w) || 0),
    viewport.xStart,
    viewport.xEnd
  ) && rangesOverlap(
    Number(rect.y) || 0,
    (Number(rect.y) || 0) + (Number(rect.h) || 0),
    viewport.yStart,
    viewport.yEnd
  )
}

export function xIntersectsViewport(x, viewport, width = 1) {
  if (!viewport) return true
  const start = Number(x) || 0
  return rangesOverlap(start, start + Math.max(0, Number(width) || 0), viewport.xStart, viewport.xEnd)
}

export function visiblePitchRange({
  viewport,
  minPitch,
  maxPitch,
  rowHeight,
  headerHeight = 0,
} = {}) {
  const row = Math.max(1, Number(rowHeight) || 1)
  const header = Number(headerHeight) || 0
  const pitchMin = Number(minPitch)
  const pitchMax = Number(maxPitch)
  const firstRow = Math.max(0, Math.floor((viewport.yStart - header) / row))
  const lastRow = Math.min(
    Math.max(0, pitchMax - pitchMin),
    Math.max(firstRow, Math.ceil((viewport.yEnd - header) / row) - 1)
  )
  return {
    minPitch: Math.max(pitchMin, pitchMax - lastRow),
    maxPitch: Math.min(pitchMax, pitchMax - firstRow),
  }
}

export function visibleBeatRange({
  viewport,
  originX = 0,
  pxPerBeat,
  clipStart = 0,
  maxBeats = Number.POSITIVE_INFINITY,
} = {}) {
  const scale = Math.max(1e-6, Number(pxPerBeat) || 1)
  const origin = Number(originX) || 0
  const start = Number(clipStart) || 0
  const firstIndex = Math.max(0, Math.floor((viewport.xStart - origin) / scale))
  const lastIndex = Math.max(firstIndex, Math.ceil((viewport.xEnd - origin) / scale))
  const clampedLast = Number.isFinite(maxBeats) ? Math.min(maxBeats, lastIndex) : lastIndex
  return {
    firstIndex,
    lastIndex: clampedLast,
    startBeat: start + firstIndex,
    endBeat: start + clampedLast,
  }
}

export function shouldRunPlaybackRedraw({
  playing = false,
  hidden = false,
  visualBeats = 0,
  transportBeats = 0,
} = {}) {
  if (hidden) return false
  if (playing) return true
  return visualBeats !== transportBeats
}
