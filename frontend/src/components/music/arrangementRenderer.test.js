import assert from 'node:assert/strict'
import test from 'node:test'

import { createArrangementRenderer } from './arrangementRenderer.js'

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
      calls.push({ type: 'fillText', args, fillStyle: this.fillStyle })
    },
    lineTo(...args) {
      this.path.push({ type: 'lineTo', args })
    },
    moveTo(...args) {
      this.path.push({ type: 'moveTo', args })
    },
    save() {},
    restore() {},
    translate() {},
    setLineDash() {},
    strokeRect() {},
    clip() {},
    closePath() {},
    arc() {},
    arcTo() {},
    setTransform() {},
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

test('drawArrangement_skipsClipsOutsideHorizontalViewport', () => {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  globalThis.window = { devicePixelRatio: 1 }
  const ctx = createRecordingContext()
  const arrangementPxPerBeat = ref(20)
  const visibleClip = {
    id: 'near',
    type: 'midi',
    name: 'Near Clip',
    start: 1,
    duration: 2,
    notes: [],
  }
  const offscreenClip = {
    id: 'far',
    type: 'midi',
    name: 'Far Clip',
    start: 80,
    duration: 2,
    notes: [],
  }

  function clipRect(clip, trackIndex) {
    return {
      x: Number(clip.start || 0) * arrangementPxPerBeat.value + 2,
      y: 48 + trackIndex * 64 + 10,
      w: Math.max(18, Number(clip.duration || 0.25) * arrangementPxPerBeat.value - 4),
      h: 44,
    }
  }

  const renderer = createArrangementRenderer({
    activeClipId: ref(null),
    activePianoSnapStep: ref(1),
    activeTrack: ref({ id: 1 }),
    arrangementCanvas: ref(createCanvas(ctx)),
    arrangementEmptyBars: 0,
    arrangementHeaderCanvas: ref(createCanvas(createRecordingContext())),
    arrangementPxPerBeat,
    arrangementRulerH: 24,
    arrangementSubtrackTop: () => 24,
    arrangementTrackH: 64,
    arrangementTrackTop: (index) => 48 + index * 64,
    arrangementVisibleSubtracks: ref([]),
    arrangementWrap: ref({
      clientWidth: 400,
      clientHeight: 240,
      scrollLeft: 0,
      scrollTop: 0,
    }),
    automationCurveHandlePoint: () => null,
    automationCurveValueAtBeat: () => 0,
    automationPointY: () => 80,
    automationTargetLabel: () => '',
    clipRect,
    currentTrackListWidth: () => 160,
    editableHarmonyEvents: () => [],
    isAutomationTrack: () => false,
    meterBeats: ref(4),
    pianoSubtrackH: 28,
    project: ref({
      length_beats: 16,
      time_signature: [4, 4],
      meter_events: [],
      harmony_events: [],
    }),
    rulerBarLabelFont: 'bar-font',
    rulerBeatLabelFont: 'beat-font',
    rulerBeatLabelMinScale: 24,
    rulerFineTickRatio: 1 / 12,
    rulerLabelGap: 2,
    rulerMajorTickRatio: 1 / 3,
    rulerMinorTickRatio: 1 / 6,
    selectedAutomationPoint: ref({ trackId: null, index: -1 }),
    selectedClipIds: ref(new Set()),
    snapStep: 1,
    sortAutomationPoints: () => 0,
    tracks: ref([
      {
        id: 1,
        name: 'Lead',
        color: '#4e79ff',
        mute: false,
        clips: [visibleClip, offscreenClip],
      },
    ]),
    visualPositionBeats: ref(0),
  })

  try {
    renderer.drawArrangement()
  } finally {
    if (hadWindow) {
      globalThis.window = previousWindow
    } else {
      delete globalThis.window
    }
  }

  const labels = ctx.calls
    .filter(call => call.type === 'fillText')
    .map(call => String(call.args[0]))
  assert.equal(labels.some(label => label.includes('Near Clip')), true)
  assert.equal(labels.some(label => label.includes('Far Clip')), false)
})

test('drawArrangement_keepsRulerLabelsInAbsoluteCanvasCoordinatesAfterHorizontalScroll', () => {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  globalThis.window = { devicePixelRatio: 1 }
  const headerCtx = createRecordingContext()
  const arrangementPxPerBeat = ref(20)
  const renderer = createArrangementRenderer({
    activeClipId: ref(null),
    activePianoSnapStep: ref(1),
    activeTrack: ref(null),
    arrangementCanvas: ref(createCanvas(createRecordingContext())),
    arrangementEmptyBars: 0,
    arrangementHeaderCanvas: ref(createCanvas(headerCtx)),
    arrangementPxPerBeat,
    arrangementRulerH: 24,
    arrangementSubtrackTop: () => 24,
    arrangementTrackH: 64,
    arrangementTrackTop: (index) => 48 + index * 64,
    arrangementVisibleSubtracks: ref([]),
    arrangementWrap: ref({
      clientWidth: 400,
      clientHeight: 240,
      scrollLeft: 160,
      scrollTop: 0,
    }),
    automationCurveHandlePoint: () => null,
    automationCurveValueAtBeat: () => 0,
    automationPointY: () => 80,
    automationTargetLabel: () => '',
    clipRect: () => ({ x: 0, y: 0, w: 0, h: 0 }),
    currentTrackListWidth: () => 0,
    editableHarmonyEvents: () => [],
    isAutomationTrack: () => false,
    meterBeats: ref(4),
    pianoSubtrackH: 28,
    project: ref({
      length_beats: 40,
      time_signature: [4, 4],
      meter_events: [],
      harmony_events: [],
    }),
    rulerBarLabelFont: 'bar-font',
    rulerBeatLabelFont: 'beat-font',
    rulerBeatLabelMinScale: 24,
    rulerFineTickRatio: 1 / 12,
    rulerLabelGap: 0,
    rulerMajorTickRatio: 1 / 3,
    rulerMinorTickRatio: 1 / 6,
    selectedAutomationPoint: ref({ trackId: null, index: -1 }),
    selectedClipIds: ref(new Set()),
    snapStep: 1,
    sortAutomationPoints: () => 0,
    tracks: ref([]),
    visualPositionBeats: ref(0),
  })

  try {
    renderer.drawArrangement()
  } finally {
    if (hadWindow) {
      globalThis.window = previousWindow
    } else {
      delete globalThis.window
    }
  }

  const rulerLabels = headerCtx.calls.filter(call => call.type === 'fillText')
  const barThree = rulerLabels.find(call => call.args[0] === '3')

  assert.equal(rulerLabels.some(call => call.args[0] === '1'), false)
  assert.equal(barThree?.args[1], 8 * arrangementPxPerBeat.value)
})
