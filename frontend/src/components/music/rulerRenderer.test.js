import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createBeatRulerRenderer,
  firstMultipleAtOrAfter,
} from './rulerRenderer.js'

function createContext(overrides = {}) {
  return {
    activePianoSnapStep: { value: 1 },
    project: { value: { time_signature: [3, 4], meter_events: [] } },
    rulerBarLabelFont: 'bar-font',
    rulerBeatLabelFont: 'beat-font',
    rulerBeatLabelMinScale: 24,
    rulerFineTickRatio: 1 / 12,
    rulerLabelGap: 2,
    rulerMajorTickRatio: 1 / 3,
    rulerMinorTickRatio: 1 / 6,
    snapStep: 0.25,
    ...overrides,
  }
}

function createCanvasContext() {
  const calls = []
  return {
    calls,
    beginPath: () => calls.push({ type: 'beginPath' }),
    fillRect: (...args) => calls.push({ type: 'fillRect', args }),
    fillText(text, x, y) {
      calls.push({
        type: 'fillText',
        text,
        x,
        y,
        fillStyle: this.fillStyle,
        font: this.font,
      })
    },
    lineTo: (...args) => calls.push({ type: 'lineTo', args }),
    moveTo: (...args) => calls.push({ type: 'moveTo', args }),
    restore: () => calls.push({ type: 'restore' }),
    save: () => calls.push({ type: 'save' }),
    stroke: () => calls.push({ type: 'stroke' }),
  }
}

test('firstMultipleAtOrAfter_returnsNextGridLineFromOrigin', () => {
  assert.equal(firstMultipleAtOrAfter(1.01, 0.25), 1.25)
  assert.equal(firstMultipleAtOrAfter(1.01, 0.5, 0.25), 1.25)
})

test('rulerTickMetrics_distinguishesBarsBeatUnitsAndFineTicks', () => {
  const { rulerTickMetrics } = createBeatRulerRenderer(createContext({
    project: { value: { time_signature: [3, 8], meter_events: [] } },
  }))

  assert.equal(rulerTickMetrics(0).heightRatio, 1 / 3)
  assert.equal(rulerTickMetrics(0.5).heightRatio, 1 / 6)
  assert.equal(rulerTickMetrics(0.25).shouldLabel, false)
})

test('drawBeatRulerLabels_usesMeterAwareBarAndBeatLabels', () => {
  const { drawBeatRulerLabels } = createBeatRulerRenderer(createContext())
  const ctx = createCanvasContext()

  drawBeatRulerLabels(ctx, {
    startBeat: 0,
    endBeat: 3,
    originX: 0,
    scale: 40,
    height: 30,
    labelY: 16,
  })

  const labels = ctx.calls
    .filter(call => call.type === 'fillText')
    .map(call => ({ text: call.text, font: call.font }))

  assert.deepEqual(labels, [
    { text: '1', font: 'bar-font' },
    { text: '1.2', font: 'beat-font' },
    { text: '1.3', font: 'beat-font' },
    { text: '2', font: 'bar-font' },
  ])
})
