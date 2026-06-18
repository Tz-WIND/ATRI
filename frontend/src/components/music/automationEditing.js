import {
  applyCurveAmount,
  curveUnitAtPosition,
  normalizeCurveAmount,
} from './controllerLanes.js'
import {
  quantizedBeatsBetween,
  snapBeatToGrid,
} from './pianoQuantize.js'
import { buildAutomationReplaceRangeOperations } from './studioIncrementalDiff.js'

export function createAutomationEditing(context) {
  const {
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
    project,
    selectedAutomationPoint,
    tracks,
  } = context

  let automationDrag = null

  function startAutomationDrag(track, point, pointerId) {
    const originalPoints = cloneAutomationPoints(ensureAutomationTrackPoints(track))
    const value = automationValueFromY(track, point.y)
    const beat = snapAutomationBeat(point.beat)
    upsertAutomationPointAt(track, beat, value)
    automationDrag = {
      type: 'automation',
      pointerId,
      trackId: track.id,
      lastBeat: beat,
      lastValue: value,
      originalPoints,
    }
    bindAutomationDrag()
    drawAll()
  }

  function startAutomationPointDrag(track, pointIndex, pointerId) {
    const points = ensureAutomationTrackPoints(track)
    const point = points[pointIndex]
    if (!point) return
    const originalPoints = cloneAutomationPoints(points)
    selectedAutomationPoint.value = { trackId: track.id, index: pointIndex }
    automationDrag = {
      type: 'automation-point',
      pointerId,
      trackId: track.id,
      pointIndex,
      originalPoints,
    }
    bindAutomationDrag()
    drawAll()
  }

  function startAutomationCurveDrag(track, hit, pointerY, pointerId) {
    if (!hit?.point) return
    const originalPoints = cloneAutomationPoints(ensureAutomationTrackPoints(track))
    selectedAutomationPoint.value = { trackId: track.id, index: hit.index }
    automationDrag = {
      type: 'automation-curve',
      pointerId,
      trackId: track.id,
      pointIndex: hit.index,
      startY: pointerY,
      startCurveAmount: Number(hit.point.curve_amount || 0),
      originalPoints,
    }
    bindAutomationDrag()
    drawAll()
  }

  function bindAutomationDrag() {
    window.addEventListener('pointermove', onAutomationPointerMove)
    window.addEventListener('pointerup', onAutomationPointerUp)
  }

  function unbindAutomationDrag() {
    window.removeEventListener('pointermove', onAutomationPointerMove)
    window.removeEventListener('pointerup', onAutomationPointerUp)
  }

  function onAutomationPointerMove(event) {
    if (!automationDrag || !project.value) return
    const track = tracks.value.find(item => Number(item.id) === Number(automationDrag.trackId))
    const point = arrangementPoint(event)
    if (!track || !point) return
    event.preventDefault()
    if (automationDrag.type === 'automation-curve') {
      const nextCurveAmount = normalizeCurveAmount(
        automationDrag.startCurveAmount
          + ((automationDrag.startY - point.y) / Math.max(1, arrangementTrackH - 24)) * curveHandleDragScale
      )
      updateAutomationPointCurve(track, automationDrag.pointIndex, nextCurveAmount)
      drawAll()
      return
    }
    const beat = snapAutomationBeat(point.beat)
    const value = automationValueFromY(track, point.y)
    if (automationDrag.type === 'automation-point') {
      automationDrag.pointIndex = moveAutomationPoint(track, automationDrag.pointIndex, beat, value)
    } else {
      writeAutomationDragPoints(
        track,
        automationDrag.lastBeat,
        automationDrag.lastValue,
        beat,
        value
      )
      automationDrag.lastBeat = beat
      automationDrag.lastValue = value
    }
    drawAll()
  }

  async function onAutomationPointerUp() {
    if (!automationDrag) return
    const drag = automationDrag
    automationDrag = null
    unbindAutomationDrag()
    await persistAutomationTrackPoints(drag.trackId, automationTrackPoints(drag.trackId), {
      previousPoints: drag.originalPoints,
      snapBeats: drag.type !== 'automation-curve',
    })
    drawAll()
  }

  function automationTrackPoints(trackId) {
    const track = tracks.value.find(item => Number(item.id) === Number(trackId))
    return ensureAutomationTrackPoints(track)
  }

  function ensureAutomationTrackPoints(track) {
    if (!track) return []
    if (!track.automation || typeof track.automation !== 'object') {
      track.automation = { points: [], value_min: 0, value_max: 1 }
    }
    if (!Array.isArray(track.automation.points)) track.automation.points = []
    return track.automation.points
  }

  function cloneAutomationPoints(points = []) {
    return (points || []).map(point => ({ ...point }))
  }

  function automationValueRange(track) {
    const min = Number(track?.automation?.value_min ?? 0)
    const max = Number(track?.automation?.value_max ?? 1)
    if (!Number.isFinite(min) || !Number.isFinite(max) || Math.abs(max - min) < 0.000001) {
      return { min: 0, max: 1 }
    }
    return min < max ? { min, max } : { min: max, max: min }
  }

  function automationValueFromY(track, y) {
    const trackIndex = tracks.value.findIndex(item => Number(item.id) === Number(track?.id))
    const top = arrangementTrackTop(Math.max(0, trackIndex)) + 12
    const bodyHeight = Math.max(1, arrangementTrackH - 24)
    const unit = 1 - clamp((Number(y || 0) - top) / bodyHeight, 0, 1)
    const { min, max } = automationValueRange(track)
    return roundAutomationValue(min + unit * (max - min))
  }

  function automationPointY(track, point, trackIndex) {
    const { min, max } = automationValueRange(track)
    const unit = clamp((Number(point?.value ?? min) - min) / Math.max(0.0001, max - min), 0, 1)
    return arrangementTrackTop(trackIndex) + 12 + (1 - unit) * (arrangementTrackH - 24)
  }

  function snapAutomationBeat(value) {
    return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
  }

  function roundAutomationValue(value) {
    return Math.round(Number(value || 0) * 1000000) / 1000000
  }

  function normalizeAutomationPoint(track, point, options = {}) {
    const { min, max } = automationValueRange(track)
    const value = clamp(roundAutomationValue(point?.value), min, max)
    const normalized = {
      beat: options.snapBeats === false
        ? Math.max(0, roundAutomationValue(point?.beat))
        : snapAutomationBeat(point?.beat),
      value,
      curve: String(point?.curve || 'linear'),
    }
    if (point?.id) normalized.id = String(point.id)
    const curveAmount = normalizeCurveAmount(point?.curve_amount ?? point?.curveAmount)
    if (Math.abs(curveAmount) > 0.000001) {
      normalized.curve_amount = curveAmount
    }
    return normalized
  }

  function sortAutomationPoints(a, b) {
    return Number(a.beat || 0) - Number(b.beat || 0)
  }

  function findAutomationPointIndex(track, beat) {
    const points = ensureAutomationTrackPoints(track)
    const snapThreshold = activePianoSnapStep.value
      ? Math.max(0.001, activePianoSnapStep.value / 3)
      : Number.POSITIVE_INFINITY
    const threshold = Math.min(Math.max(0.008, 3 / arrangementPxPerBeat.value), snapThreshold)
    return points.findIndex(point => Math.abs(Number(point.beat || 0) - Number(beat || 0)) <= threshold)
  }

  function upsertAutomationPointAt(track, beat, value) {
    const points = ensureAutomationTrackPoints(track)
    const point = normalizeAutomationPoint(track, { beat, value })
    const index = findAutomationPointIndex(track, point.beat)
    if (index >= 0) points[index] = { ...points[index], ...point }
    else points.push(point)
    points.sort(sortAutomationPoints)
  }

  function moveAutomationPoint(track, pointIndex, beat, value) {
    const points = ensureAutomationTrackPoints(track)
    if (!points[pointIndex]) return pointIndex
    const point = normalizeAutomationPoint(track, { ...points[pointIndex], beat, value })
    points.splice(pointIndex, 1, point)
    points.sort(sortAutomationPoints)
    const nextIndex = points.findIndex(item => item === point)
    selectedAutomationPoint.value = { trackId: track.id, index: nextIndex >= 0 ? nextIndex : pointIndex }
    return selectedAutomationPoint.value.index
  }

  function updateAutomationPointCurve(track, pointIndex, curveAmount) {
    const points = ensureAutomationTrackPoints(track)
    if (!points[pointIndex]) return
    points[pointIndex] = applyCurveAmount(points[pointIndex], curveAmount)
    selectedAutomationPoint.value = { trackId: track.id, index: pointIndex }
  }

  function hitTestAutomationPoint(track, x, y, trackIndex) {
    const points = ensureAutomationTrackPoints(track)
    for (let index = points.length - 1; index >= 0; index -= 1) {
      const point = points[index]
      const px = Number(point.beat || 0) * arrangementPxPerBeat.value
      const py = automationPointY(track, point, trackIndex)
      if (Math.hypot(x - px, y - py) <= automationPointHitRadius) {
        return { point, index }
      }
    }
    return null
  }

  function hitTestAutomationCurveHandle(track, x, y, trackIndex) {
    const indexedPoints = ensureAutomationTrackPoints(track)
      .map((point, index) => ({ point, index }))
      .sort((a, b) => sortAutomationPoints(a.point, b.point))
    for (let index = indexedPoints.length - 2; index >= 0; index -= 1) {
      const left = indexedPoints[index]
      const right = indexedPoints[index + 1]
      const handle = automationCurveHandlePoint(track, left.point, right.point, trackIndex)
      if (handle && Math.hypot(x - handle.x, y - handle.y) <= automationCurveHandleHitRadius) {
        return {
          point: left.point,
          index: left.index,
          startBeat: Number(left.point.beat || 0),
          endBeat: Number(right.point.beat || 0),
        }
      }
    }
    return null
  }

  function writeAutomationDragPoints(track, startBeat, startValue, endBeat, endValue) {
    const beats = quantizedBeatsBetween(startBeat, endBeat, activePianoSnapStep.value)
    for (const beat of beats) {
      const value = interpolateAutomationValue(startBeat, startValue, endBeat, endValue, beat)
      upsertAutomationPointAt(track, beat, value)
    }
  }

  function interpolateAutomationValue(startBeat, startValue, endBeat, endValue, beat) {
    const distance = Number(endBeat || 0) - Number(startBeat || 0)
    if (Math.abs(distance) < 0.000001) return roundAutomationValue(endValue)
    const unit = (Number(beat || 0) - Number(startBeat || 0)) / distance
    return roundAutomationValue(Number(startValue || 0) + (Number(endValue || 0) - Number(startValue || 0)) * unit)
  }

  function automationCurveValueAtBeat(track, left, right, beat) {
    if (!left || !right) return 0
    if (String(left.curve || 'linear') === 'hold') return roundAutomationValue(left.value)
    const startBeat = Number(left.beat || 0)
    const endBeat = Number(right.beat || 0)
    const span = endBeat - startBeat
    if (span <= 0.000001) return roundAutomationValue(right.value)
    const position = clamp((Number(beat || 0) - startBeat) / span, 0, 1)
    const { min, max } = automationValueRange(track)
    const range = Math.max(0.000001, max - min)
    const startUnit = clamp((Number(left.value ?? min) - min) / range, 0, 1)
    const endUnit = clamp((Number(right.value ?? min) - min) / range, 0, 1)
    return roundAutomationValue(min + curveUnitAtPosition(
      startUnit,
      endUnit,
      position,
      left.curve_amount
    ) * range)
  }

  async function persistAutomationTrackPoints(trackId, points, options = {}) {
    const track = tracks.value.find(item => Number(item.id) === Number(trackId))
    const previous = (options.previousPoints || ensureAutomationTrackPoints(track))
      .map(point => normalizeAutomationPoint(track, point, options))
      .sort(sortAutomationPoints)
    const normalized = (points || [])
      .map(point => normalizeAutomationPoint(track, point, options))
      .sort(sortAutomationPoints)
    const operations = buildAutomationReplaceRangeOperations(previous, normalized)
    await diffAutomationTrack(trackId, operations)
  }

  function automationCurveHandlePoint(track, left, right, trackIndex) {
    if (!left || !right || String(left.curve || 'linear') === 'hold') return null
    const startBeat = Number(left.beat || 0)
    const endBeat = Number(right.beat || 0)
    if (endBeat <= startBeat) return null
    if ((endBeat - startBeat) * arrangementPxPerBeat.value < curveHandleMinSegmentPx) return null
    const beat = startBeat + (endBeat - startBeat) * 0.5
    const value = automationCurveValueAtBeat(track, left, right, beat)
    return {
      x: beat * arrangementPxPerBeat.value,
      y: automationPointY(track, { beat, value }, trackIndex),
    }
  }

  function cancelAutomationDrag() {
    automationDrag = null
    unbindAutomationDrag()
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value))
  }

  return {
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
  }
}
