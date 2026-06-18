import {
  controllerCurveValueAtBeat,
  controllerLaneColorStyles,
  controllerRenderPoints,
  controllerValueToUnit,
} from './controllerLanes.js'
import {
  clamp,
  curveRenderSampleCount,
  hexToRgba,
  roundRect,
  setupCanvas,
} from './canvasUtils.js'
import {
  meterBarLinesBetween,
  meterSegments,
  normalizeMeterEvents,
} from './meterEvents.js'
import { createBeatRulerRenderer, firstMultipleAtOrAfter } from './rulerRenderer.js'

export function createPianoRollRenderer(context) {
  const {
    activeMidiClip,
    activePianoSnapStep,
    controllerLaneBodyH,
    controllerLaneCanvases,
    controllerLaneH,
    controllerLaneTabH,
    controllerLanes,
    controllerScrollLeft,
    controllerWrap,
    controllerDefinitionForLane,
    curveHandleMinSegmentPx,
    draftNote,
    editableHarmonyEvents,
    maxPitch,
    meterBeats,
    minPitch,
    noteRect,
    pianoCanvas,
    pianoEmptyBars,
    pianoHarmonyLaneTop,
    pianoHarmonyLaneVisible,
    pianoHeaderCanvas,
    pianoKeyW,
    pianoMeterLaneH,
    pianoMeterLaneTop,
    pianoMeterLaneVisible,
    pianoNoteTop,
    pianoPxPerBeat,
    pianoRowH,
    pianoRulerH,
    pianoSubtrackH,
    pianoTimelineWidth,
    pianoVisible,
    pianoVisibleSubtracks,
    pianoWrap,
    project,
    rulerBarLabelFont,
    rulerBeatLabelFont,
    rulerBeatLabelMinScale,
    rulerFineTickRatio,
    rulerLabelGap,
    rulerMajorTickRatio,
    rulerMinorTickRatio,
    selectedControllerEventId,
    selectedNoteIds,
    selectionBox,
    snapStep,
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

  function pianoLengthBeats(clip) {
    const emptyTailBeats = pianoEmptyBars * Math.max(1, meterBeats.value)
    const clipStart = Number(clip.start || 0)
    const noteEnd = Math.max(
      0,
      ...(clip.notes || []).map((note) => Number(note.start || 0) + Number(note.duration || 0))
    )
    const meterEventEnd = Math.max(
      0,
      ...(project.value?.meter_events || []).map(event => Number(event.beat || 0) - clipStart)
    )
    const harmonyEventEnd = Math.max(
      0,
      ...(project.value?.harmony_events || []).map(event => Number(event.beat || 0) - clipStart)
    )
    return Math.max(
      Number(clip.duration || 4),
      emptyTailBeats,
      noteEnd + 2,
      meterEventEnd + 2,
      harmonyEventEnd + 2
    )
  }

  function drawPiano() {
    const headerCanvas = pianoHeaderCanvas.value
    const canvas = pianoCanvas.value
    const wrap = pianoWrap.value
    if (!headerCanvas || !canvas || !wrap || !activeMidiClip.value || !pianoVisible.value) return
    const clip = activeMidiClip.value.clip
    const width = Math.max(
      wrap.clientWidth,
      pianoKeyW + pianoLengthBeats(clip) * pianoPxPerBeat.value
    )
    pianoTimelineWidth.value = width
    const headerHeight = pianoNoteTop.value
    const bodyHeight = (maxPitch - minPitch + 1) * pianoRowH
    const headerCtx = setupCanvas(headerCanvas, width, headerHeight)
    const bodyCtx = setupCanvas(canvas, width, bodyHeight)
    drawPianoHeader(headerCtx, width, clip)
    drawPianoBody(bodyCtx, width, bodyHeight, clip)
  }

  function drawPianoHeader(ctx, width, clip) {
    const height = pianoNoteTop.value
    ctx.fillStyle = '#17191c'
    ctx.fillRect(0, 0, width, height)
    drawPianoRuler(ctx, width, clip)
    for (const subtrackId of pianoVisibleSubtracks.value) {
      if (subtrackId === 'meter') {
        drawPianoMeterLane(ctx, width, clip)
      } else if (subtrackId === 'harmony') {
        drawPianoHarmonyLane(ctx, width, clip, pianoHarmonyLaneTop.value)
      }
    }
    drawPianoPlayhead(ctx, height, clip)
  }

  function drawPianoBody(ctx, width, height, clip) {
    const logicalHeight = pianoNoteTop.value + height
    ctx.save()
    ctx.translate(0, -pianoNoteTop.value)
    ctx.fillStyle = '#17191c'
    ctx.fillRect(0, pianoNoteTop.value, width, height)
    for (let pitch = maxPitch; pitch >= minPitch; pitch -= 1) {
      const row = maxPitch - pitch
      const y = pianoNoteTop.value + row * pianoRowH
      const black = [1, 3, 6, 8, 10].includes(pitch % 12)
      ctx.fillStyle = black ? '#111316' : '#202326'
      ctx.fillRect(0, y, pianoKeyW, pianoRowH)
      ctx.fillStyle = black ? 'rgba(255,255,255,0.035)' : 'rgba(255,255,255,0.018)'
      ctx.fillRect(pianoKeyW, y, width - pianoKeyW, pianoRowH)
      ctx.strokeStyle = black ? 'rgba(0,0,0,0.38)' : 'rgba(229,236,245,0.08)'
      ctx.beginPath()
      ctx.moveTo(0, y + pianoRowH)
      ctx.lineTo(width, y + pianoRowH)
      ctx.stroke()
      if (pitch % 12 === 0) {
        ctx.fillStyle = '#9aa3ad'
        ctx.font = '10px Cascadia Mono, Consolas, monospace'
        ctx.fillText(pitchName(pitch), 10, y + 9)
      }
    }
    paintPianoGrid(ctx, width, logicalHeight, clip)

    const track = activeMidiClip.value.track
    if (track) {
      for (const note of clip.notes || []) {
        if (note.pitch < minPitch || note.pitch > maxPitch) continue
        const rect = noteRect(note)
        const selected = selectedNoteIds.value.has(note.id)
        ctx.fillStyle = selected ? '#f0d17a' : hexToRgba(track.color, 0.82)
        roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
        ctx.fill()
        ctx.strokeStyle = selected ? 'rgba(255, 255, 255, 0.64)' : 'rgba(0,0,0,0.24)'
        ctx.stroke()
        if (rect.w > 34) {
          ctx.fillStyle = selected ? 'rgba(20,22,24,0.9)' : 'rgba(255,255,255,0.82)'
          ctx.font = '10px Cascadia Mono, Consolas, monospace'
          ctx.fillText(pitchName(note.pitch), rect.x + 5, rect.y + 9)
        }
      }
    }
    if (draftNote.value) {
      const rect = noteRect(draftNote.value)
      ctx.fillStyle = 'rgba(240, 209, 122, 0.52)'
      ctx.strokeStyle = 'rgba(240, 209, 122, 0.96)'
      roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
      ctx.fill()
      ctx.stroke()
    }
    if (selectionBox.value) {
      const box = selectionBox.value
      const x = Math.min(box.x1, box.x2)
      const y = Math.min(box.y1, box.y2)
      const w = Math.abs(box.x2 - box.x1)
      const h = Math.abs(box.y2 - box.y1)
      ctx.fillStyle = 'rgba(125, 168, 232, 0.12)'
      ctx.strokeStyle = 'rgba(125, 168, 232, 0.72)'
      ctx.setLineDash([4, 3])
      ctx.strokeRect(x, y, w, h)
      ctx.fillRect(x, y, w, h)
      ctx.setLineDash([])
    }
    drawPianoPlayhead(ctx, logicalHeight, clip)
    ctx.restore()
  }

  function drawControllerLanes() {
    if (!pianoVisible.value || !activeMidiClip.value || !controllerLanes.value.length) return
    const clip = activeMidiClip.value.clip
    controllerScrollLeft.value = controllerWrap.value?.scrollLeft || 0
    const width = Math.max(
      controllerWrap.value?.clientWidth || 0,
      pianoTimelineWidth.value,
      pianoKeyW + pianoLengthBeats(clip) * pianoPxPerBeat.value
    )
    pianoTimelineWidth.value = width
    for (const lane of controllerLanes.value) {
      const canvas = controllerLaneCanvases.get(lane.id)
      if (!canvas) continue
      const ctx = setupCanvas(canvas, width, controllerLaneH)
      drawControllerLane(ctx, lane, width, clip)
    }
  }

  function drawControllerLane(ctx, lane, width, clip) {
    const definition = controllerDefinitionForLane(lane)
    const colorStyles = controllerLaneColorStyles(activeMidiClip.value?.track?.color)
    ctx.fillStyle = '#17191c'
    ctx.fillRect(0, 0, width, controllerLaneH)
    ctx.fillStyle = '#202428'
    ctx.fillRect(0, 0, pianoKeyW, controllerLaneH)
    ctx.fillStyle = '#202326'
    ctx.fillRect(pianoKeyW, 0, width - pianoKeyW, controllerLaneTabH)
    ctx.fillStyle = '#181b1f'
    ctx.fillRect(pianoKeyW, controllerLaneTabH, width - pianoKeyW, controllerLaneBodyH)
    paintControllerGrid(ctx, width, clip)

    if (definition.type === 'velocity') {
      drawVelocityLane(ctx, clip, definition, colorStyles)
    } else {
      drawEventLane(ctx, clip, definition, colorStyles)
    }
    drawControllerPlayhead(ctx, controllerLaneH, clip)
  }

  function paintControllerGrid(ctx, width, clip) {
    const bodyTop = controllerLaneTabH
    const bodyBottom = controllerLaneTabH + controllerLaneBodyH
    const scale = pianoPxPerBeat.value
    const snapStepWidth = activePianoSnapStep.value ? activePianoSnapStep.value * scale : 0
    ctx.strokeStyle = 'rgba(229,236,245,0.12)'
    ctx.beginPath()
    ctx.moveTo(0, bodyTop + 0.5)
    ctx.lineTo(width, bodyTop + 0.5)
    ctx.moveTo(0, bodyBottom - 0.5)
    ctx.lineTo(width, bodyBottom - 0.5)
    ctx.stroke()

    for (const unit of [0.25, 0.5, 0.75]) {
      const y = bodyTop + controllerLaneBodyH * unit
      ctx.strokeStyle = unit === 0.5 ? 'rgba(229,236,245,0.11)' : 'rgba(229,236,245,0.055)'
      ctx.beginPath()
      ctx.moveTo(pianoKeyW, y)
      ctx.lineTo(width, y)
      ctx.stroke()
    }

    const visibleBeats = Math.ceil((width - pianoKeyW) / pianoPxPerBeat.value)
    for (let beat = 0; beat <= visibleBeats; beat += 1) {
      const x = pianoKeyW + beat * pianoPxPerBeat.value
      ctx.strokeStyle = 'rgba(229,236,245,0.06)'
      ctx.beginPath()
      ctx.moveTo(x, bodyTop)
      ctx.lineTo(x, bodyBottom)
      ctx.stroke()

      if (snapStepWidth >= 4 && activePianoSnapStep.value && activePianoSnapStep.value < 1) {
        for (let subBeat = activePianoSnapStep.value; subBeat < 1; subBeat += activePianoSnapStep.value) {
          const subX = x + subBeat * scale
          ctx.strokeStyle = 'rgba(229,236,245,0.035)'
          ctx.beginPath()
          ctx.moveTo(subX, bodyTop)
          ctx.lineTo(subX, bodyBottom)
          ctx.stroke()
        }
      }
    }

    const clipStart = Number(clip.start || 0)
    const endBeat = clipStart + visibleBeats
    for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
      const barX = pianoKeyW + (line.beat - clipStart) * pianoPxPerBeat.value
      ctx.strokeStyle = 'rgba(229,236,245,0.14)'
      ctx.beginPath()
      ctx.moveTo(barX, bodyTop)
      ctx.lineTo(barX, bodyBottom)
      ctx.stroke()
    }
  }

  function drawVelocityLane(ctx, clip, definition, colorStyles) {
    const notes = clip.notes || []
    for (const note of notes) {
      const x = pianoKeyW + Number(note.start || 0) * pianoPxPerBeat.value
      const value = clamp(Math.round(Number(note.velocity || definition.defaultValue)), 1, 127)
      const y = controllerValueToY(value, definition)
      const selected = selectedNoteIds.value.has(note.id)
      ctx.strokeStyle = selected ? colorStyles.selectedVelocityStroke : colorStyles.velocityStroke
      ctx.lineWidth = selected ? 3 : 2
      ctx.beginPath()
      ctx.moveTo(x, controllerLaneTabH + controllerLaneBodyH)
      ctx.lineTo(x, y)
      ctx.stroke()
      ctx.fillStyle = selected ? colorStyles.selectedVelocityFill : colorStyles.velocityFill
      ctx.fillRect(x - 2, y - 2, 4, 4)
    }
    ctx.lineWidth = 1
  }

  function drawEventLane(ctx, clip, definition, colorStyles) {
    const tailBeat = Math.max(0, (pianoTimelineWidth.value - pianoKeyW) / pianoPxPerBeat.value)
    const points = controllerRenderPoints(clip.events || [], definition, tailBeat)
    if (!points.length) return

    ctx.strokeStyle = colorStyles.eventStroke
    ctx.fillStyle = colorStyles.eventFill
    ctx.lineWidth = 1.4
    ctx.beginPath()
    drawControllerCurvePath(ctx, points, definition)
    ctx.stroke()
    drawControllerCurveHandles(ctx, points, definition, colorStyles)

    for (const point of points) {
      if (point.synthetic) continue
      const x = pianoKeyW + Number(point.start || 0) * pianoPxPerBeat.value
      const y = controllerValueToY(point.value, definition)
      const selected = point.event?.id && point.event.id === selectedControllerEventId.value
      ctx.beginPath()
      ctx.arc(x, y, selected ? 5 : 4, 0, Math.PI * 2)
      ctx.fillStyle = selected ? '#f0d17a' : colorStyles.eventFill
      ctx.fill()
      ctx.strokeStyle = selected ? 'rgba(255,255,255,0.72)' : colorStyles.eventPointStroke
      ctx.stroke()
    }
    ctx.lineWidth = 1
  }

  function drawControllerCurvePath(ctx, points, definition) {
    points.forEach((point, index) => {
      const x = pianoKeyW + Number(point.start || 0) * pianoPxPerBeat.value
      const y = controllerValueToY(point.value, definition)
      if (index === 0) {
        ctx.moveTo(x, y)
        return
      }
      const left = points[index - 1]
      if (left.event && point.event && Number(point.start || 0) > Number(left.start || 0)) {
        drawControllerCurveSegment(ctx, left, point, definition)
      } else {
        ctx.lineTo(x, y)
      }
    })
  }

  function drawControllerCurveSegment(ctx, left, right, definition) {
    const startBeat = Number(left.start || 0)
    const endBeat = Number(right.start || 0)
    const sampleCount = curveRenderSampleCount(startBeat, endBeat, pianoPxPerBeat.value)
    for (let sample = 1; sample <= sampleCount; sample += 1) {
      const position = sample / sampleCount
      const sampleBeat = startBeat + (endBeat - startBeat) * position
      const value = controllerCurveValueAtBeat(left.event, right.event, sampleBeat, definition)
      ctx.lineTo(
        pianoKeyW + sampleBeat * pianoPxPerBeat.value,
        controllerValueToY(value, definition)
      )
    }
  }

  function drawControllerCurveHandles(ctx, points, definition, colorStyles) {
    ctx.save()
    for (let index = 0; index < points.length - 1; index += 1) {
      const handle = controllerCurveHandlePoint(points[index], points[index + 1], definition)
      if (!handle) continue
      const selected = points[index].event?.id && points[index].event.id === selectedControllerEventId.value
      ctx.beginPath()
      ctx.arc(handle.x, handle.y, selected ? 4 : 3.5, 0, Math.PI * 2)
      ctx.fillStyle = '#181b1f'
      ctx.strokeStyle = selected ? '#f0d17a' : colorStyles.eventStroke
      ctx.lineWidth = selected ? 1.5 : 1.2
      ctx.fill()
      ctx.stroke()
    }
    ctx.restore()
  }

  function controllerCurveHandlePoint(left, right, definition) {
    if (!left?.event || !right?.event) return null
    const startBeat = Number(left.start || 0)
    const endBeat = Number(right.start || 0)
    if (endBeat <= startBeat) return null
    if ((endBeat - startBeat) * pianoPxPerBeat.value < curveHandleMinSegmentPx) return null
    const beat = startBeat + (endBeat - startBeat) * 0.5
    const value = controllerCurveValueAtBeat(left.event, right.event, beat, definition)
    return {
      x: pianoKeyW + beat * pianoPxPerBeat.value,
      y: controllerValueToY(value, definition),
    }
  }

  function controllerValueToY(value, definition) {
    const unit = controllerValueToUnit(definition, value)
    return controllerLaneTabH + (1 - unit) * controllerLaneBodyH
  }

  function drawControllerPlayhead(ctx, height, clip) {
    const localBeat = visualPositionBeats.value - Number(clip.start || 0)
    if (localBeat < 0 || localBeat > Number(clip.duration || 0)) return
    const x = pianoKeyW + localBeat * pianoPxPerBeat.value
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.8)'
    ctx.lineWidth = 1.3
    ctx.beginPath()
    ctx.moveTo(x, controllerLaneTabH)
    ctx.lineTo(x, height)
    ctx.stroke()
  }

  function drawPianoRuler(ctx, width, clip) {
    const scale = pianoPxPerBeat.value
    const clipStart = Number(clip.start || 0)
    const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
    const endBeat = clipStart + visibleBeats
    ctx.fillStyle = '#202326'
    ctx.fillRect(0, 0, width, pianoRulerH)
    ctx.fillStyle = '#181b1f'
    ctx.fillRect(0, 0, pianoKeyW, pianoRulerH)
    ctx.strokeStyle = 'rgba(229,236,245,0.11)'
    ctx.beginPath()
    ctx.moveTo(0, pianoRulerH - 0.5)
    ctx.lineTo(width, pianoRulerH - 0.5)
    ctx.stroke()

    ctx.font = '10px Cascadia Mono, Consolas, monospace'

    // Quarter-note grid lines
    const firstBeat = Math.ceil(clipStart - 0.000001)
    for (let absoluteBeat = firstBeat; absoluteBeat <= endBeat + 0.001; absoluteBeat += 1) {
      const x = pianoKeyW + (absoluteBeat - clipStart) * scale
      ctx.strokeStyle = 'rgba(229,236,245,0.1)'
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, pianoRulerH)
      ctx.stroke()
    }

    // Beat-unit tick marks (only when they fall at non-integer positions and there's room)
    for (const segment of meterSegments(project.value, clipStart, endBeat)) {
      const unit = segment.beatUnit
      if (!Number.isFinite(unit) || unit <= 0 || Math.abs(unit - 1) < 0.0001 || unit * scale < 12) continue
      for (
        let absoluteBeat = firstMultipleAtOrAfter(segment.start, unit, segment.anchor);
        absoluteBeat <= segment.end + 0.001;
        absoluteBeat += unit
      ) {
        if (Math.abs(absoluteBeat - Math.round(absoluteBeat)) < 0.0001) continue // already drawn as quarter-note line
        const x = pianoKeyW + (absoluteBeat - clipStart) * scale
        ctx.strokeStyle = 'rgba(229,236,245,0.06)'
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.lineTo(x, pianoRulerH)
        ctx.stroke()
      }
    }

    for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
      const barBeat = line.beat
      const x = pianoKeyW + (barBeat - clipStart) * scale
      ctx.strokeStyle = 'rgba(240, 209, 122, 0.28)'
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, pianoRulerH)
      ctx.stroke()
    }

    drawBeatRulerLabels(ctx, {
      startBeat: clipStart,
      endBeat,
      originX: pianoKeyW,
      scale,
      height: pianoRulerH,
      labelY: 16,
    })
  }

  function drawPianoMeterLane(ctx, width, clip) {
    if (!pianoMeterLaneVisible.value) return
    const scale = pianoPxPerBeat.value
    const clipStart = Number(clip.start || 0)
    const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
    const endBeat = clipStart + visibleBeats
    const top = pianoMeterLaneTop.value
    const bottom = top + pianoMeterLaneH

    ctx.fillStyle = '#191d21'
    ctx.fillRect(0, top, width, pianoMeterLaneH)
    ctx.fillStyle = '#252b30'
    ctx.fillRect(0, top, pianoKeyW, pianoMeterLaneH)
    ctx.strokeStyle = 'rgba(229,236,245,0.12)'
    ctx.beginPath()
    ctx.moveTo(0, top + 0.5)
    ctx.lineTo(width, top + 0.5)
    ctx.moveTo(0, bottom - 0.5)
    ctx.lineTo(width, bottom - 0.5)
    ctx.stroke()

    ctx.fillStyle = '#aeb8c5'
    ctx.font = '10px Cascadia Mono, Consolas, monospace'
    ctx.fillText('Meter', 10, top + 17)

    for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
      const x = pianoKeyW + (line.beat - clipStart) * scale
      ctx.strokeStyle = 'rgba(240, 209, 122, 0.16)'
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
    }

    for (const event of normalizeMeterEvents(project.value)) {
      if (event.beat < clipStart - 0.001 || event.beat > endBeat + 0.001) continue
      const x = pianoKeyW + (event.beat - clipStart) * scale
      ctx.fillStyle = 'rgba(240, 209, 122, 0.18)'
      roundRect(ctx, x - 3, top + 4, 6, pianoMeterLaneH - 8, 3)
      ctx.fill()
      ctx.fillStyle = '#f0d17a'
      ctx.font = '11px Cascadia Mono, Consolas, monospace'
      ctx.fillText(`${event.numerator}/${event.denominator}`, x + 5, top + 18)
    }
  }

  function drawPianoHarmonyLane(ctx, width, clip, top) {
    if (!pianoHarmonyLaneVisible.value) return
    const scale = pianoPxPerBeat.value
    const clipStart = Number(clip.start || 0)
    const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
    const endBeat = clipStart + visibleBeats
    const bottom = top + pianoSubtrackH

    ctx.fillStyle = '#181c22'
    ctx.fillRect(0, top, width, pianoSubtrackH)
    ctx.fillStyle = '#252b30'
    ctx.fillRect(0, top, pianoKeyW, pianoSubtrackH)
    ctx.strokeStyle = 'rgba(229,236,245,0.12)'
    ctx.beginPath()
    ctx.moveTo(0, top + 0.5)
    ctx.lineTo(width, top + 0.5)
    ctx.moveTo(0, bottom - 0.5)
    ctx.lineTo(width, bottom - 0.5)
    ctx.stroke()

    ctx.fillStyle = '#aeb8c5'
    ctx.font = '10px Cascadia Mono, Consolas, monospace'
    ctx.fillText('Harmony', 10, top + 17)

    for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
      const x = pianoKeyW + (line.beat - clipStart) * scale
      ctx.strokeStyle = 'rgba(125, 168, 232, 0.12)'
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
    }

    for (const event of editableHarmonyEvents()) {
      if (event.beat < clipStart - 0.001 || event.beat > endBeat + 0.001) continue
      const x = pianoKeyW + (event.beat - clipStart) * scale
      ctx.fillStyle = 'rgba(125, 168, 232, 0.18)'
      roundRect(ctx, x - 3, top + 4, 6, pianoSubtrackH - 8, 3)
      ctx.fill()
      ctx.fillStyle = '#b8d0ff'
      ctx.font = '11px Cascadia Mono, Consolas, monospace'
      ctx.fillText(event.text, x + 5, top + 18)
    }
  }

  function paintPianoGrid(ctx, width, height, clip) {
    const scale = pianoPxPerBeat.value
    const clipStart = Number(clip.start || 0)
    const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
    const snapStepWidth = activePianoSnapStep.value ? activePianoSnapStep.value * scale : 0

    for (let beat = 0; beat <= visibleBeats; beat += 1) {
      const x = pianoKeyW + beat * scale
      ctx.strokeStyle = 'rgba(229,236,245,0.075)'
      ctx.lineWidth = 0.5
      ctx.beginPath()
      ctx.moveTo(x, pianoNoteTop.value)
      ctx.lineTo(x, height)
      ctx.stroke()

      if (snapStepWidth >= 4 && activePianoSnapStep.value && activePianoSnapStep.value < 1) {
        ctx.strokeStyle = 'rgba(229,236,245,0.035)'
        for (let subBeat = activePianoSnapStep.value; subBeat < 1; subBeat += activePianoSnapStep.value) {
          const subX = x + subBeat * scale
          ctx.beginPath()
          ctx.moveTo(subX, pianoNoteTop.value)
          ctx.lineTo(subX, height)
          ctx.stroke()
        }
      }
    }

    const endBeat = clipStart + visibleBeats
    for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
      const barBeat = line.beat
      const x = pianoKeyW + (barBeat - clipStart) * scale
      ctx.strokeStyle = 'rgba(240, 209, 122, 0.24)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, pianoNoteTop.value)
      ctx.lineTo(x, height)
      ctx.stroke()
    }
  }

  function drawPianoPlayhead(ctx, height, clip) {
    const localBeat = visualPositionBeats.value - Number(clip.start || 0)
    const visibleLength = pianoLengthBeats(clip)
    if (localBeat < 0 || localBeat > visibleLength) return
    const x = pianoKeyW + localBeat * pianoPxPerBeat.value
    ctx.strokeStyle = '#f0d17a'
    ctx.lineWidth = 1.6
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
    ctx.fillStyle = '#f0d17a'
    ctx.beginPath()
    ctx.moveTo(x, pianoRulerH)
    ctx.lineTo(x - 5, pianoRulerH - 8)
    ctx.lineTo(x + 5, pianoRulerH - 8)
    ctx.closePath()
    ctx.fill()
  }

  function pitchName(pitch) {
    const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    return `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`
  }

  return {
    controllerCurveHandlePoint,
    controllerValueToY,
    drawControllerLanes,
    drawPiano,
  }
}
