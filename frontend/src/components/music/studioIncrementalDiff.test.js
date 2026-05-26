import assert from 'node:assert/strict'
import {
  buildAutomationReplaceRangeOperations,
  buildClipDiffOperations,
  buildMidiEventDiffOperations,
  buildMidiNoteDiffOperations,
} from './studioIncrementalDiff.js'

const noteBefore = [
  { id: 'a', pitch: 60, start: 0, duration: 0.5, velocity: 90 },
  { id: 'b', pitch: 64, start: 1, duration: 0.5, velocity: 96 },
]

assert.deepEqual(
  buildMidiNoteDiffOperations(noteBefore, [
    { id: 'a', pitch: 62, start: 0.25, duration: 0.75, velocity: 88 },
    { id: 'c', pitch: 67, start: 2, duration: 0.5, velocity: 100 },
  ], 'clip_1'),
  [
    { op: 'delete_note', clip_id: 'clip_1', id: 'b' },
    {
      op: 'update_note',
      clip_id: 'clip_1',
      id: 'a',
      pitch: 62,
      local_start: 0.25,
      duration: 0.75,
      velocity: 88,
    },
    {
      op: 'add_note',
      clip_id: 'clip_1',
      note: { id: 'c', pitch: 67, local_start: 2, duration: 0.5, velocity: 100 },
    },
  ]
)

const eventsBefore = [
  { id: 'evt_a', type: 'control_change', start: 0, channel: 0, controller: 1, value: 32 },
  { id: 'evt_b', type: 'pitch_bend', start: 1, channel: 0, value: 200 },
]

assert.deepEqual(
  buildMidiEventDiffOperations(eventsBefore, [
    {
      id: 'evt_a',
      type: 'control_change',
      start: 0.5,
      channel: 0,
      controller: 1,
      value: 64,
      curve_amount: 0.25,
    },
    { id: 'evt_c', type: 'channel_pressure', start: 2, channel: 0, pressure: 70 },
  ], 'clip_1'),
  [
    { op: 'delete_event', clip_id: 'clip_1', id: 'evt_b' },
    {
      op: 'update_event',
      clip_id: 'clip_1',
      id: 'evt_a',
      event: {
        id: 'evt_a',
        type: 'control_change',
        local_start: 0.5,
        channel: 0,
        controller: 1,
        value: 64,
        curve_amount: 0.25,
      },
    },
    {
      op: 'add_event',
      clip_id: 'clip_1',
      event: {
        id: 'evt_c',
        type: 'channel_pressure',
        local_start: 2,
        channel: 0,
        pressure: 70,
      },
    },
  ]
)

assert.deepEqual(
  buildAutomationReplaceRangeOperations(
    [{ id: 'pt_a', beat: 0, value: 0.2 }, { id: 'pt_b', beat: 8, value: 0.7 }],
    [{ id: 'pt_a', beat: 0, value: 0.4, curve: 'linear' }],
  ),
  [
    {
      op: 'replace_range',
      start: 0,
      end: 8,
      points: [{ id: 'pt_a', beat: 0, value: 0.4, curve: 'linear' }],
    },
  ]
)

const previousClips = [
  {
    trackId: 1,
    clip: {
      id: 'clip_a',
      type: 'midi',
      name: 'A',
      start: 0,
      duration: 2,
      notes: [{ id: 'n1', pitch: 60, start: 0, duration: 1, velocity: 96 }],
      events: [],
    },
  },
  {
    trackId: 1,
    clip: { id: 'clip_b', type: 'midi', name: 'B', start: 4, duration: 1, notes: [], events: [] },
  },
]

assert.deepEqual(
  buildClipDiffOperations(previousClips, [
    {
      trackId: 2,
      clip: {
        id: 'clip_a',
        type: 'midi',
        name: 'A',
        start: 8,
        duration: 3,
        notes: [{ id: 'n1', pitch: 60, start: 0, duration: 1, velocity: 96 }],
        events: [],
      },
    },
    {
      trackId: 1,
      clip: { id: 'clip_c', type: 'midi', name: 'C', start: 2, duration: 1, notes: [], events: [] },
    },
  ]),
  [
    { op: 'delete_clip', clip_id: 'clip_b' },
    {
      op: 'update_clip',
      clip_id: 'clip_a',
      track_id: 2,
      clip: {
        id: 'clip_a',
        type: 'midi',
        name: 'A',
        start: 8,
        duration: 3,
        notes: [{ id: 'n1', pitch: 60, start: 0, duration: 1, velocity: 96 }],
        events: [],
      },
    },
    {
      op: 'add_clip',
      track_id: 1,
      clip: { id: 'clip_c', type: 'midi', name: 'C', start: 2, duration: 1, notes: [], events: [] },
    },
  ]
)

assert.deepEqual(buildMidiNoteDiffOperations(noteBefore, noteBefore, 'clip_1'), [])
assert.deepEqual(buildMidiEventDiffOperations(eventsBefore, eventsBefore, 'clip_1'), [])
assert.deepEqual(
  buildAutomationReplaceRangeOperations([{ id: 'pt_a', beat: 0, value: 0.2 }], [{ id: 'pt_a', beat: 0, value: 0.2 }]),
  []
)
assert.deepEqual(buildClipDiffOperations(previousClips, previousClips), [])
