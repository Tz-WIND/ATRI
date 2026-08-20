import {
  effectiveMeterAtBeat,
  meterBarLinesBetween,
  meterPositionAtBeat,
} from './meterEvents.js'

export function createBeatRulerRenderer(context) {
  const {
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
  } = context

  function isRulerBarBeat(absoluteBeat) {
    const beat = Number(absoluteBeat || 0)
    return meterBarLinesBetween(project.value, Math.max(0, beat - 0.0001), beat + 0.0001)
      .some(line => Math.abs(line.beat - beat) < 0.0001)
  }

  function rulerBeatLabel(absoluteBeat) {
    const position = meterPositionAtBeat(project.value, absoluteBeat)
    return position.beat === 1 ? String(position.bar) : `${position.bar}.${position.beat}`
  }

  function rulerTickStep() {
    return activePianoSnapStep.value || snapStep
  }

  function isRulerBeatUnitTick(absoluteBeat) {
    const meter = effectiveMeterAtBeat(project.value, absoluteBeat)
    const unit = 4 / meter.denominator
    if (!Number.isFinite(unit) || unit <= 0) return false
    const beat = Number(absoluteBeat || 0) / unit
    return Math.abs(beat - Math.round(beat)) < 0.0001
  }

  function rulerTickMetrics(absoluteBeat) {
    const isBar = isRulerBarBeat(absoluteBeat)
    if (isBar) {
      return {
        isBar,
        shouldLabel: true,
        heightRatio: rulerMajorTickRatio,
        lineWidth: 1.4,
        strokeStyle: 'rgba(229, 236, 245, 0.58)',
        fillStyle: '#d9e2ec',
      }
    }
    const isBeat = isRulerBeatUnitTick(absoluteBeat)
    if (isBeat) {
      return {
        isBar,
        shouldLabel: true,
        heightRatio: rulerMinorTickRatio,
        lineWidth: 1,
        strokeStyle: 'rgba(229, 236, 245, 0.38)',
        fillStyle: '#b9c8d8',
      }
    }
    return {
      isBar,
      shouldLabel: false,
      heightRatio: rulerFineTickRatio,
      lineWidth: 0.7,
      strokeStyle: 'rgba(229, 236, 245, 0.2)',
      fillStyle: '#95b6d8',
    }
  }

  function drawBeatRulerLabels(ctx, {
    startBeat,
    visibleStartBeat = startBeat,
    endBeat,
    originX,
    scale,
    height,
    labelY,
  }) {
    const tickStep = rulerTickStep()
    if (!Number.isFinite(tickStep) || tickStep <= 0 || !Number.isFinite(scale) || scale <= 0) return

    ctx.save()
    ctx.textBaseline = 'middle'
    ctx.fillStyle = 'rgba(32, 35, 38, 0.94)'
    ctx.fillRect(originX, 0, Math.max(0, (endBeat - startBeat) * scale), height)
    ctx.strokeStyle = 'rgba(229, 236, 245, 0.16)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(originX, height - 0.5)
    ctx.lineTo(originX + Math.max(0, (endBeat - startBeat) * scale), height - 0.5)
    ctx.stroke()

    for (
      let absoluteBeat = firstMultipleAtOrAfter(visibleStartBeat, tickStep);
      absoluteBeat <= endBeat + 0.001;
      absoluteBeat += tickStep
    ) {
      const metrics = rulerTickMetrics(absoluteBeat)
      const shouldDrawBeatLabel = metrics.shouldLabel && (metrics.isBar || scale >= rulerBeatLabelMinScale)

      const x = originX + (absoluteBeat - startBeat) * scale
      const tickBottom = height - 1
      const tickHeight = Math.max(4, Math.round(height * metrics.heightRatio))
      ctx.strokeStyle = metrics.strokeStyle
      ctx.lineWidth = metrics.lineWidth
      ctx.beginPath()
      ctx.moveTo(x, tickBottom - tickHeight)
      ctx.lineTo(x, tickBottom)
      ctx.stroke()

      if (!shouldDrawBeatLabel) continue
      const labelX = Math.max(originX + rulerLabelGap, x + rulerLabelGap)
      ctx.font = metrics.isBar ? rulerBarLabelFont : rulerBeatLabelFont
      ctx.fillStyle = metrics.fillStyle
      ctx.fillText(rulerBeatLabel(absoluteBeat), labelX, Math.min(labelY, tickBottom - tickHeight - 4))
    }

    ctx.restore()
  }

  return {
    drawBeatRulerLabels,
    isRulerBarBeat,
    rulerTickMetrics,
  }
}

export function firstMultipleAtOrAfter(value, step, origin = 0) {
  return origin + Math.ceil(((Number(value || 0) - origin) - 0.000001) / step) * step
}
