import assert from 'node:assert/strict'
import test from 'node:test'

import { createAutomationEditing } from './automationEditing.js'

function ref(value) {
  return { value }
}

function withFakeWindow(run) {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  const listeners = new Map()
  globalThis.window = {
    addEventListener(type, listener) {
      listeners.set(type, listener)
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type)
    },
  }

  return Promise.resolve()
    .then(() => run(listeners))
    .finally(() => {
      if (hadWindow) {
        globalThis.window = previousWindow
      } else {
        delete globalThis.window
      }
    })
}

function createAutomationContext(overrides = {}) {
  const track = {
    id: 7,
    automation: {
      value_min: 0,
      value_max: 1,
      points: [{ beat: 0, value: 0.2, curve: 'linear' }],
    },
  }
  return {
    activePianoSnapStep: ref(1),
    arrangementPoint: () => null,
    arrangementPxPerBeat: 24,
    arrangementTrackH: 100,
    arrangementTrackTop: () => 0,
    automationCurveHandleHitRadius: 6,
    automationPointHitRadius: 6,
    curveHandleDragScale: 1,
    curveHandleMinSegmentPx: 20,
    diffAutomationTrack: async () => ({ ok: true }),
    drawAll: () => {},
    project: ref({}),
    selectedAutomationPoint: ref({ trackId: null, index: null }),
    track,
    tracks: ref([track]),
    ...overrides,
  }
}

test('automationPointerUp_rollsBackAndReportsPersistFailures', async () => {
  await withFakeWindow(async (listeners) => {
    let drawCount = 0
    let reportedError = null
    const context = createAutomationContext({
      diffAutomationTrack: async () => {
        throw new Error('network down')
      },
      drawAll: () => {
        drawCount += 1
      },
      onAutomationPersistError: (error) => {
        reportedError = error
      },
    })
    const editor = createAutomationEditing(context)

    editor.startAutomationDrag(context.track, { beat: 2, y: 20 }, 1)
    assert.equal(context.track.automation.points.length, 2)

    await assert.doesNotReject(() => listeners.get('pointerup')())

    assert.deepEqual(context.track.automation.points, [
      { beat: 0, value: 0.2, curve: 'linear' },
    ])
    assert.equal(reportedError.message, 'network down')
    assert.equal(listeners.has('pointerup'), false)
    assert.equal(drawCount, 2)
  })
})
