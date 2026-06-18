import assert from 'node:assert/strict'
import test from 'node:test'

import { controllerDefinitionFromId } from './controllerLanes.js'
import { createPianoRollRenderer } from './pianoRollRenderer.js'

function ref(value) {
  return { value }
}

function createRecordingContext() {
  const calls = []
  const context = {
    calls,
    beginPath() {
      this.path = []
    },
    fill() {
      calls.push({ type: 'fill', fillStyle: this.fillStyle })
    },
    fillRect(...args) {
      calls.push({ type: 'fillRect', args, fillStyle: this.fillStyle })
    },
    fillText(...args) {
      calls.push({ type: 'fillText', args, fillStyle: this.fillStyle, font: this.font })
    },
    lineTo(...args) {
      this.path.push({ type: 'lineTo', args })
    },
    moveTo(...args) {
      this.path.push({ type: 'moveTo', args })
    },
    setTransform(...args) {
      calls.push({ type: 'setTransform', args })
    },
    stroke() {
      calls.push({ type: 'stroke', path: [...this.path], strokeStyle: this.strokeStyle })
    },
  }
  context.path = []
  return context
}

function createCanvas(ctx) {
  return {
    style: {},
    getContext(type) {
      assert.equal(type, '2d')
      return ctx
    },
  }
}

test('controllerLaneGrid_usesMeterMapForBarLinesWithinClip', () => {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  globalThis.window = { devicePixelRatio: 1 }
  const ctx = createRecordingContext()
  const lane = { id: 'velocity', activeControllerId: 'velocity' }
  const controllerLaneCanvases = new Map([[lane.id, createCanvas(ctx)]])
  const pianoKeyW = 40
  const pianoPxPerBeat = ref(20)
  const clip = {
    start: 0,
    duration: 11,
    notes: [],
    events: [],
  }
  const renderer = createPianoRollRenderer({
    activeMidiClip: ref({ clip, track: { color: '#12abef' } }),
    activePianoSnapStep: ref(1),
    controllerLaneBodyH: 70,
    controllerLaneCanvases,
    controllerLaneH: 98,
    controllerLaneTabH: 28,
    controllerLanes: ref([lane]),
    controllerScrollLeft: ref(0),
    controllerWrap: ref({ clientWidth: pianoKeyW + 11 * pianoPxPerBeat.value, scrollLeft: 0 }),
    controllerDefinitionForLane: () => controllerDefinitionFromId('velocity'),
    curveHandleMinSegmentPx: 20,
    draftNote: ref(null),
    editableHarmonyEvents: () => [],
    maxPitch: 72,
    meterBeats: ref(4),
    minPitch: 48,
    noteRect: () => ({ x: 0, y: 0, w: 0, h: 0 }),
    pianoCanvas: ref(null),
    pianoEmptyBars: 0,
    pianoHarmonyLaneTop: ref(0),
    pianoHarmonyLaneVisible: ref(false),
    pianoHeaderCanvas: ref(null),
    pianoKeyW,
    pianoMeterLaneH: 28,
    pianoMeterLaneTop: ref(0),
    pianoMeterLaneVisible: ref(false),
    pianoNoteTop: ref(0),
    pianoPxPerBeat,
    pianoRowH: 14,
    pianoRulerH: 24,
    pianoSubtrackH: 28,
    pianoTimelineWidth: ref(pianoKeyW + 11 * pianoPxPerBeat.value),
    pianoVisible: ref(true),
    pianoVisibleSubtracks: ref([]),
    pianoWrap: ref(null),
    project: ref({
      time_signature: [4, 4],
      meter_events: [{ beat: 4, numerator: 3, denominator: 4 }],
    }),
    rulerBarLabelFont: 'bar-font',
    rulerBeatLabelFont: 'beat-font',
    rulerBeatLabelMinScale: 24,
    rulerFineTickRatio: 1 / 12,
    rulerLabelGap: 2,
    rulerMajorTickRatio: 1 / 3,
    rulerMinorTickRatio: 1 / 6,
    selectedControllerEventId: ref(null),
    selectedNoteIds: ref(new Set()),
    selectionBox: ref(null),
    snapStep: 1,
    visualPositionBeats: ref(99),
  })

  try {
    renderer.drawControllerLanes()
  } finally {
    if (hadWindow) {
      globalThis.window = previousWindow
    } else {
      delete globalThis.window
    }
  }

  const barLineXs = ctx.calls
    .filter(call => call.type === 'stroke' && call.strokeStyle === 'rgba(229,236,245,0.14)')
    .map(call => call.path[0].args[0])

  assert.deepEqual(barLineXs, [40, 120, 180, 240])
})
