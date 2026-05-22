import assert from 'node:assert/strict'
import test from 'node:test'

import {
  effectiveMeterAtBeat,
  meterBarLinesBetween,
  meterPositionAtBeat,
  normalizeMeterEvents,
} from './meterEvents.js'

test('normalizeMeterEvents_keepsProjectMeterAtZeroAndSortsDedicatedEvents', () => {
  const project = {
    time_signature: [4, 4],
    meter_events: [
      { beat: 8, numerator: 3, denominator: 4 },
      { beat: 14, numerator: 5, denominator: 8 },
    ],
  }

  assert.deepEqual(normalizeMeterEvents(project), [
    { beat: 0, numerator: 4, denominator: 4 },
    { beat: 8, numerator: 3, denominator: 4 },
    { beat: 14, numerator: 5, denominator: 8 },
  ])
})

test('effectiveMeterAtBeat_usesDedicatedMeterEventsUntilNextEvent', () => {
  const project = {
    time_signature: [4, 4],
    meter_events: [
      { beat: 8, numerator: 3, denominator: 4 },
      { beat: 14, numerator: 5, denominator: 8 },
    ],
  }

  assert.deepEqual(effectiveMeterAtBeat(project, 7.99), { numerator: 4, denominator: 4 })
  assert.deepEqual(effectiveMeterAtBeat(project, 8), { numerator: 3, denominator: 4 })
  assert.deepEqual(effectiveMeterAtBeat(project, 14.5), { numerator: 5, denominator: 8 })
})

test('meterBarLinesBetween_returnsBarStartsForEachMeterSegmentWithoutGlobalRerender', () => {
  const project = {
    time_signature: [4, 4],
    meter_events: [
      { beat: 8, numerator: 3, denominator: 4 },
      { beat: 14, numerator: 5, denominator: 8 },
    ],
  }

  assert.deepEqual(meterBarLinesBetween(project, 0, 18).map(line => line.beat), [
    0,
    4,
    8,
    11,
    14,
    16.5,
  ])
})

test('meterPositionAtBeat_numbersBarsAndBeatsAcrossMeterChanges', () => {
  const project = {
    time_signature: [4, 4],
    meter_events: [
      { beat: 8, numerator: 3, denominator: 4 },
      { beat: 14, numerator: 5, denominator: 8 },
    ],
  }

  assert.deepEqual(meterPositionAtBeat(project, 9), {
    bar: 3,
    beat: 2,
    ticks: 0,
    numerator: 3,
    denominator: 4,
  })
  assert.deepEqual(meterPositionAtBeat(project, 14.5), {
    bar: 5,
    beat: 2,
    ticks: 0,
    numerator: 5,
    denominator: 8,
  })
})

test('meterPositionAtBeat_startsNewMeterSegmentAtExactEventBeat', () => {
  const project = {
    time_signature: [4, 4],
    meter_events: [
      { beat: 6, numerator: 3, denominator: 4 },
    ],
  }

  assert.deepEqual(meterPositionAtBeat(project, 6), {
    bar: 3,
    beat: 1,
    ticks: 0,
    numerator: 3,
    denominator: 4,
  })
})
