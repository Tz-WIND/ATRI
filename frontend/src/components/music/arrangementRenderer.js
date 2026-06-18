import {
  meterBarLinesBetween,
  normalizeMeterEvents,
} from './meterEvents.js'
import {
  clamp,
  curveRenderSampleCount,
  hexToRgba,
  mixHexColor,
  roundRect,
  setupCanvas,
} from './canvasUtils.js'
import { createBeatRulerRenderer } from './rulerRenderer.js'

export function createArrangementRenderer(context) {
  const {
    activeClipId,
    activePianoSnapStep,
    activeTrack,
    arrangementCanvas,
    arrangementEmptyBars,
    arrangementHeaderCanvas,
    arrangementPxPerBeat,
    arrangementRulerH,
    arrangementSubtrackTop,
    arrangementTrackH,
    arrangementTrackTop,
    arrangementVisibleSubtracks,
    arrangementWrap,
    automationCurveHandlePoint,
    automationCurveValueAtBeat,
    automationPointY,
    automationTargetLabel,
    clipRect,
    currentTrackListWidth,
    editableHarmonyEvents,
    isAutomationTrack,
    meterBeats,
    pianoSubtrackH,
    project,
    rulerBarLabelFont,
    rulerBeatLabelFont,
    rulerBeatLabelMinScale,
    rulerFineTickRatio,
    rulerLabelGap,
    rulerMajorTickRatio,
    rulerMinorTickRatio,
    selectedAutomationPoint,
    selectedClipIds,
    snapStep,
    sortAutomationPoints,
    tracks,
    visualPositionBeats,
  } = context

  const { drawBeatRulerLabels } = createBeatRulerRenderer({
    activePianoSnapStep,
    project,
    rulerBarLabelFont,
    rulerBeatLabelFont,
    rulerBeatLabelMinScale,
    rulerFineTickRatio,
    rulerLabelGap,
    rulerMajorTickRatio,
    rulerMinorTickRatio,
    snapStep,
  })

  function drawArrangement() {
    const headerCanvas = arrangementHeaderCanvas.value
    const canvas = arrangementCanvas.value
    const wrap = arrangementWrap.value
    if (!headerCanvas || !canvas || !wrap) return
    const timelineViewportWidth = Math.max(
      0,
      arrangementWrap.value.clientWidth - currentTrackListWidth()
    )
    const width = Math.max(
      timelineViewportWidth,
      arrangementLengthBeats() * arrangementPxPerBeat.value + 40
    )
    const headerHeight = arrangementTrackTop(0)
    const bodyHeight = Math.max(
      220 - headerHeight,
      Math.max(1, tracks.value.length) * arrangementTrackH
    )
    const headerCtx = setupCanvas(headerCanvas, width, headerHeight)
    const bodyCtx = setupCanvas(canvas, width, bodyHeight)
    drawArrangementHeader(headerCtx, width)
    drawArrangementBody(bodyCtx, width, bodyHeight)
  }

  function drawArrangementHeader(ctx, width) {
    const height = arrangementTrackTop(0)
    ctx.fillStyle = '#17191c'
    ctx.fillRect(0, 0, width, height)
    ctx.fillStyle = '#202326'
    ctx.fillRect(0, 0, width, arrangementRulerH)
    drawRuler(ctx, width)
    drawArrangementMeterLane(ctx, width, arrangementSubtrackTop('meter'))
    drawArrangementHarmonyLane(ctx, width, arrangementSubtrackTop('harmony'))
    drawPlayhead(ctx, height)
  }

  function drawArrangementBody(ctx, width, height) {
    const logicalHeight = arrangementTrackTop(0) + height
    ctx.save()
    ctx.translate(0, -arrangementTrackTop(0))
    ctx.fillStyle = '#17191c'
    ctx.fillRect(0, arrangementTrackTop(0), width, height)

    tracks.value.forEach((track, index) => {
      const y = arrangementTrackTop(index)
      ctx.fillStyle = activeTrack.value?.id === track.id ? 'rgba(158, 191, 255, 0.08)' : '#1b1d20'
      ctx.fillRect(0, y, width, arrangementTrackH)
    })

    paintGrid(ctx, width, logicalHeight, 0, arrangementRulerH)

    tracks.value.forEach((track, index) => {
      const y = arrangementTrackTop(index)
      ctx.strokeStyle = 'rgba(229, 236, 245, 0.11)'
      ctx.beginPath()
      ctx.moveTo(0, y + arrangementTrackH)
      ctx.lineTo(width, y + arrangementTrackH)
      ctx.stroke()
    })

    tracks.value.forEach((track, index) => {
      if (isAutomationTrack(track)) {
        drawAutomationTrack(ctx, track, index)
        return
      }
      for (const clip of track.clips || []) {
        drawArrangementClip(ctx, track, clip, index)
      }
    })
    drawPlayhead(ctx, logicalHeight)
    ctx.restore()
  }

  function arrangementLengthBeats() {
    const emptyTailBeats = arrangementEmptyBars * Math.max(1, meterBeats.value)
    const clipEnd = Math.max(
      0,
      ...tracks.value.flatMap(track => (track.clips || []).map((clip) => (
        Number(clip.start || 0) + Number(clip.duration || 0)
      )))
    )
    const automationEnd = Math.max(
      0,
      ...tracks.value.flatMap(track => (track.automation?.points || []).map(point => Number(point.beat || 0)))
    )
    const harmonyEnd = Math.max(
      0,
      ...(project.value?.harmony_events || []).map(event => Number(event.beat || 0))
    )
    return Math.max(
      Number(project.value?.length_beats || 16),
      emptyTailBeats,
      clipEnd + 2,
      automationEnd + 2,
      harmonyEnd + 2
    )
  }

  function drawArrangementClip(ctx, track, clip, trackIndex) {
    const rect = clipRect(clip, trackIndex)
    const selected = selectedClipIds.value.has(clip.id)
    const active = activeClipId.value === clip.id
    if (clip.type === 'audio') {
      drawZrythmAudioRegionFrame(ctx, clip, rect, track, selected, active)
      drawClipAudioPreview(ctx, clip, rect, track)
      drawZrythmAudioResizeHandle(ctx, rect, active)
      return
    }

    ctx.fillStyle = hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.78)
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
    }

    ctx.fillStyle = 'rgba(255,255,255,0.35)'
    ctx.fillRect(rect.x + rect.w - 5, rect.y + 18, 2, rect.h - 24)
  }

  function drawAutomationTrack(ctx, track, trackIndex) {
    const points = Array.isArray(track?.automation?.points)
      ? [...track.automation.points].sort(sortAutomationPoints)
      : []
    const y = arrangementTrackTop(trackIndex)
    const midY = y + arrangementTrackH * 0.5
    const left = 0
    const right = arrangementLengthBeats() * arrangementPxPerBeat.value
    ctx.strokeStyle = hexToRgba(track.color, track.mute ? 0.22 : 0.74)
    ctx.lineWidth = 2
    ctx.beginPath()
    if (!points.length) {
      ctx.moveTo(left, midY)
      ctx.lineTo(right, midY)
    } else {
      drawAutomationHoldLine(ctx, track, points, trackIndex, right)
    }
    ctx.stroke()
    drawAutomationCurveHandles(ctx, track, points, trackIndex)
    for (const [index, point] of points.entries()) {
      const x = Number(point.beat || 0) * arrangementPxPerBeat.value
      const py = automationPointY(track, point, trackIndex)
      const selected = Number(selectedAutomationPoint.value.trackId) === Number(track.id)
        && selectedAutomationPoint.value.index === index
      ctx.fillStyle = selected ? '#f0d17a' : hexToRgba(track.color, track.mute ? 0.28 : 0.95)
      ctx.beginPath()
      ctx.arc(x, py, selected ? 5 : 4, 0, Math.PI * 2)
      ctx.fill()
      if (selected) {
        ctx.strokeStyle = 'rgba(255,255,255,0.72)'
        ctx.lineWidth = 1.2
        ctx.stroke()
      }
    }
    ctx.fillStyle = 'rgba(244,246,248,0.72)'
    ctx.font = '10px Cascadia Mono, Consolas, monospace'
    ctx.fillText(automationTargetLabel(track.target), 8, y + 16)
  }

  function drawAutomationSegmentPath(ctx, track, points, trackIndex, right) {
    const first = points[0]
    const last = points[points.length - 1]
    const firstX = Number(first.beat || 0) * arrangementPxPerBeat.value
    const firstY = automationPointY(track, first, trackIndex)
    ctx.moveTo(0, firstY)
    ctx.lineTo(firstX, firstY)
    for (let index = 0; index < points.length - 1; index += 1) {
      const left = points[index]
      const rightPoint = points[index + 1]
      drawAutomationSegment(ctx, track, left, rightPoint, trackIndex)
    }
    ctx.lineTo(right, automationPointY(track, last, trackIndex))
  }

  function drawAutomationSegment(ctx, track, left, right, trackIndex) {
    const startBeat = Number(left.beat || 0)
    const endBeat = Number(right.beat || 0)
    const rightX = endBeat * arrangementPxPerBeat.value
    const rightY = automationPointY(track, right, trackIndex)
    if (endBeat <= startBeat) {
      ctx.lineTo(rightX, rightY)
      return
    }
    if (String(left.curve || 'linear') === 'hold') {
      ctx.lineTo(rightX, automationPointY(track, left, trackIndex))
      ctx.lineTo(rightX, rightY)
      return
    }
    const sampleCount = curveRenderSampleCount(startBeat, endBeat, arrangementPxPerBeat.value)
    for (let sample = 1; sample <= sampleCount; sample += 1) {
      const position = sample / sampleCount
      const sampleBeat = startBeat + (endBeat - startBeat) * position
      const value = automationCurveValueAtBeat(track, left, right, sampleBeat)
      ctx.lineTo(
        sampleBeat * arrangementPxPerBeat.value,
        automationPointY(track, { beat: sampleBeat, value }, trackIndex)
      )
    }
  }

  function drawAutomationCurveHandles(ctx, track, points, trackIndex) {
    ctx.save()
    for (let index = 0; index < points.length - 1; index += 1) {
      const handle = automationCurveHandlePoint(track, points[index], points[index + 1], trackIndex)
      if (!handle) continue
      const selected = Number(selectedAutomationPoint.value.trackId) === Number(track.id)
        && selectedAutomationPoint.value.index === index
      ctx.beginPath()
      ctx.arc(handle.x, handle.y, selected ? 4 : 3.5, 0, Math.PI * 2)
      ctx.fillStyle = '#181b1f'
      ctx.strokeStyle = selected ? '#f0d17a' : hexToRgba(track.color, track.mute ? 0.3 : 0.84)
      ctx.lineWidth = selected ? 1.5 : 1.2
      ctx.fill()
      ctx.stroke()
    }
    ctx.restore()
  }

  function drawAutomationHoldLine(ctx, track, points, trackIndex, right) {
    drawAutomationSegmentPath(ctx, track, points, trackIndex, right)
  }

  function drawZrythmAudioRegionFrame(ctx, clip, rect, track, selected, active) {
    const trackColor = clip.color || track.color
    const headerHeight = audioRegionHeaderHeight(rect)
    const radius = 5

    ctx.save()
    ctx.beginPath()
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius)
    ctx.clip()

    ctx.fillStyle = hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.72)
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h)

    ctx.fillStyle = hexToRgba(trackColor, track.mute ? 0.18 : 0.78)
    ctx.fillRect(rect.x, rect.y + headerHeight, rect.w, Math.max(1, rect.h - headerHeight))

    ctx.fillStyle = hexToRgba(mixHexColor(trackColor, '#ffffff', 0.32), track.mute ? 0.24 : 0.72)
    ctx.fillRect(rect.x, rect.y, rect.w, headerHeight)

    ctx.strokeStyle = 'rgba(255,255,255,0.18)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(rect.x, rect.y + headerHeight + 0.5)
    ctx.lineTo(rect.x + rect.w, rect.y + headerHeight + 0.5)
    ctx.stroke()
    ctx.restore()

    ctx.strokeStyle = active
      ? 'rgba(240, 209, 122, 0.95)'
      : selected ? 'rgba(255,255,255,0.62)' : 'rgba(0,0,0,0.32)'
    ctx.lineWidth = active ? 2 : 1
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius)
    ctx.stroke()

    ctx.fillStyle = zrythmRegionContentColor()
    ctx.font = '10px Cascadia Mono, Consolas, monospace'
    ctx.fillText(`AUDIO  ${clip.name || 'Clip'}`, rect.x + 7, rect.y + headerHeight - 5)
  }

  function drawZrythmAudioResizeHandle(ctx, rect, active) {
    const headerHeight = audioRegionHeaderHeight(rect)
    ctx.fillStyle = active ? 'rgba(240, 209, 122, 0.78)' : 'rgba(255,255,255,0.42)'
    ctx.fillRect(rect.x + rect.w - 5, rect.y + headerHeight + 3, 2, Math.max(1, rect.h - headerHeight - 8))
  }

  function audioRegionHeaderHeight(rect) {
    return Math.min(18, Math.max(14, Math.floor(rect.h * 0.24)))
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

  function drawClipAudioPreview(ctx, clip, rect, track) {
    const waveform = Array.isArray(clip.waveform) ? clip.waveform : []
    const points = waveform.map(waveformPointMetrics).filter(Boolean)
    const trackColor = clip.color || track.color
    const bodyTop = rect.y + audioRegionHeaderHeight(rect) + 2
    const bodyBottom = rect.y + rect.h - 5
    const bodyHeight = Math.max(12, bodyBottom - bodyTop)
    const mid = bodyTop + bodyHeight * 0.5
    const maxAmp = Math.max(4, bodyHeight * 0.46)
    const left = rect.x + 4
    const right = Math.max(left + 1, rect.x + rect.w - 7)
    const bounds = {
      left,
      right,
      top: bodyTop,
      bottom: bodyBottom,
      height: bodyHeight,
      mid,
      maxAmp,
      width: Math.max(1, right - left),
    }

    ctx.save()
    ctx.beginPath()
    roundRect(ctx, rect.x + 3, bodyTop, Math.max(1, rect.w - 9), bodyHeight, 3)
    ctx.clip()

    ctx.fillStyle = hexToRgba(trackColor, track.mute ? 0.08 : 0.18)
    ctx.fillRect(rect.x + 3, bodyTop, Math.max(1, rect.w - 9), bodyHeight)

    if (points.length) {
      drawZrythmWaveformEnvelope(ctx, points, bounds)
    } else {
      drawZrythmFallbackWaveform(ctx, bounds)
    }

    ctx.restore()
  }

  function waveformPointMetrics(point) {
    if (typeof point === 'number') {
      const peak = clamp(Math.abs(point), 0, 1)
      return { min: -peak, max: peak, rms: peak * 0.58, peak }
    }
    if (!point || typeof point !== 'object') return null

    let min = waveformFiniteNumber(point.min)
    let max = waveformFiniteNumber(point.max)
    const rawPeak = waveformFiniteNumber(point.peak)
    const rawRms = waveformFiniteNumber(point.rms)
    let peak = rawPeak === null ? null : clamp(Math.abs(rawPeak), 0, 1)
    let rms = rawRms === null ? null : clamp(Math.abs(rawRms), 0, 1)

    if (min === null && max === null) {
      if (peak === null) return null
      min = -peak
      max = peak
    } else {
      const fallback = peak || 0
      min = min === null ? -Math.max(fallback, Math.abs(max || 0)) : clamp(min, -1, 1)
      max = max === null ? Math.max(fallback, Math.abs(min || 0)) : clamp(max, -1, 1)
      if (min > max) {
        const nextMin = max
        max = min
        min = nextMin
      }
    }

    const envelopePeak = Math.max(Math.abs(min), Math.abs(max))
    rms = rms === null ? envelopePeak * 0.58 : rms
    peak = peak === null ? envelopePeak : peak
    peak = clamp(Math.max(peak, envelopePeak, rms), 0, 1)
    rms = Math.min(rms, peak)
    return { min, max, rms, peak }
  }

  function waveformFiniteNumber(value) {
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  }

  function drawZrythmWaveformEnvelope(ctx, points, bounds) {
    if (!points.length) return
    ctx.save()
    ctx.fillStyle = zrythmRegionContentColor()
    ctx.strokeStyle = zrythmRegionOutlineColor()
    ctx.lineWidth = 1
    ctx.lineJoin = 'round'
    ctx.beginPath()
    points.forEach((point, index) => {
      const x = waveformX(bounds, points.length, index)
      const y = waveformY(bounds, point.min)
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    for (let index = points.length - 1; index >= 0; index -= 1) {
      ctx.lineTo(waveformX(bounds, points.length, index), waveformY(bounds, points[index].max))
    }
    ctx.closePath()
    ctx.fill()
    ctx.stroke()
    ctx.restore()
  }

  function drawZrythmFallbackWaveform(ctx, bounds) {
    const count = Math.max(32, Math.floor(bounds.width))
    const points = Array.from({ length: count }, (_, index) => {
      const unit = index / Math.max(1, count - 1)
      const peak = clamp(
        0.18 + Math.abs(Math.sin(unit * 31.4)) * 0.36 + Math.abs(Math.sin(unit * 91.7)) * 0.2,
        0,
        1
      )
      return { min: -peak, max: peak, rms: peak * 0.54, peak }
    })
    drawZrythmWaveformEnvelope(ctx, points, bounds)
  }

  function waveformX(bounds, count, index) {
    return bounds.left + (index / Math.max(1, count - 1)) * bounds.width
  }

  function waveformY(bounds, value) {
    return clamp(bounds.mid + value * bounds.maxAmp, bounds.top + 1, bounds.bottom - 1)
  }

  function drawRuler(ctx, width) {
    const scale = arrangementPxPerBeat.value
    const endBeat = Math.ceil(width / scale)
    drawBeatRulerLabels(ctx, {
      startBeat: 0,
      endBeat,
      originX: 0,
      scale,
      height: arrangementRulerH,
      labelY: 19,
    })
  }

  function drawArrangementMeterLane(ctx, width, top) {
    if (!arrangementVisibleSubtracks.value.includes('meter')) return
    const scale = arrangementPxPerBeat.value
    const endBeat = Math.ceil(width / scale)
    const bottom = top + pianoSubtrackH

    ctx.fillStyle = '#191d21'
    ctx.fillRect(0, top, width, pianoSubtrackH)
    ctx.strokeStyle = 'rgba(229,236,245,0.12)'
    ctx.beginPath()
    ctx.moveTo(0, top + 0.5)
    ctx.lineTo(width, top + 0.5)
    ctx.moveTo(0, bottom - 0.5)
    ctx.lineTo(width, bottom - 0.5)
    ctx.stroke()

    for (const line of meterBarLinesBetween(project.value, 0, endBeat)) {
      const x = line.beat * scale
      ctx.strokeStyle = 'rgba(240, 209, 122, 0.16)'
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
    }

    for (const event of normalizeMeterEvents(project.value)) {
      if (event.beat > endBeat + 0.001) continue
      const x = Number(event.beat || 0) * scale
      ctx.fillStyle = 'rgba(240, 209, 122, 0.18)'
      roundRect(ctx, x + 2, top + 4, 6, pianoSubtrackH - 8, 3)
      ctx.fill()
      ctx.fillStyle = '#f0d17a'
      ctx.font = '11px Cascadia Mono, Consolas, monospace'
      ctx.fillText(`${event.numerator}/${event.denominator}`, x + 11, top + 18)
    }
  }

  function drawArrangementHarmonyLane(ctx, width, top) {
    if (!arrangementVisibleSubtracks.value.includes('harmony')) return
    const scale = arrangementPxPerBeat.value
    const endBeat = Math.ceil(width / scale)
    const bottom = top + pianoSubtrackH

    ctx.fillStyle = '#181c22'
    ctx.fillRect(0, top, width, pianoSubtrackH)
    ctx.strokeStyle = 'rgba(229,236,245,0.12)'
    ctx.beginPath()
    ctx.moveTo(0, top + 0.5)
    ctx.lineTo(width, top + 0.5)
    ctx.moveTo(0, bottom - 0.5)
    ctx.lineTo(width, bottom - 0.5)
    ctx.stroke()

    for (const line of meterBarLinesBetween(project.value, 0, endBeat)) {
      const x = line.beat * scale
      ctx.strokeStyle = 'rgba(125, 168, 232, 0.12)'
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
    }

    for (const event of editableHarmonyEvents()) {
      if (event.beat > endBeat + 0.001) continue
      const x = Number(event.beat || 0) * scale
      ctx.fillStyle = 'rgba(125, 168, 232, 0.18)'
      roundRect(ctx, x + 2, top + 4, 6, pianoSubtrackH - 8, 3)
      ctx.fill()
      ctx.fillStyle = '#b8d0ff'
      ctx.font = '11px Cascadia Mono, Consolas, monospace'
      ctx.fillText(event.text, x + 11, top + 18)
    }
  }

  function paintGrid(ctx, width, height, offsetX, offsetY) {
    const scale = arrangementPxPerBeat.value
    const beats = Math.ceil((width - offsetX) / scale)

    for (let beat = 0; beat <= beats; beat += 1) {
      const x = offsetX + beat * scale
      ctx.strokeStyle = 'rgba(229,236,245,0.07)'
      ctx.lineWidth = 0.5
      ctx.beginPath()
      ctx.moveTo(x, offsetY)
      ctx.lineTo(x, height)
      ctx.stroke()

      ctx.strokeStyle = 'rgba(229,236,245,0.035)'
      for (let div = 1; div < 4; div += 1) {
        const subX = x + (div * scale) / 4
        ctx.beginPath()
        ctx.moveTo(subX, offsetY)
        ctx.lineTo(subX, height)
        ctx.stroke()
      }
    }

    // Bar lines follow the project meter map so piano-roll meter changes affect the arrangement grid.
    for (const line of meterBarLinesBetween(project.value, 0, beats)) {
      const barX = offsetX + line.beat * scale
      ctx.strokeStyle = 'rgba(229,236,245,0.18)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(barX, offsetY)
      ctx.lineTo(barX, height)
      ctx.stroke()
    }
  }

  function drawPlayhead(ctx, height, offsetX = 0) {
    const x = offsetX + visualPositionBeats.value * arrangementPxPerBeat.value
    ctx.strokeStyle = '#d7b66f'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
    ctx.fillStyle = '#d7b66f'
    ctx.beginPath()
    ctx.moveTo(x, arrangementRulerH)
    ctx.lineTo(x - 5, arrangementRulerH - 8)
    ctx.lineTo(x + 5, arrangementRulerH - 8)
    ctx.closePath()
    ctx.fill()
  }

  function zrythmRegionContentColor() {
    return 'rgba(246, 250, 255, 0.84)'
  }

  function zrythmRegionOutlineColor() {
    return 'rgba(255, 255, 255, 0.96)'
  }

  return {
    drawArrangement,
  }
}
