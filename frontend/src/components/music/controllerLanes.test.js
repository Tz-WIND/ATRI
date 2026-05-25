import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  DEFAULT_NOTE_VELOCITY,
  createDefaultControllerLanes,
  controllerDefinitionFromId,
  controllerDisplayRange,
  controllerDisplayValue,
  controllerLaneStackHeight,
  controllerLaneColorStyles,
  controllerCurveValueAtBeat,
  controllerRenderPoints,
  controllerUnitToValue,
  controllerValueToUnit,
  eventMatchesController,
  applyCurveAmount,
  normalizeControllerEvent,
} from './controllerLanes.js'

test('DEFAULT_NOTE_VELOCITY_matchesPianoDrawDefault', () => {
  assert.equal(DEFAULT_NOTE_VELOCITY, 80)
})

test('createDefaultControllerLanes_returnsSingleVelocityLane', () => {
  const lanes = createDefaultControllerLanes()

  assert.equal(lanes.length, 1)
  assert.equal(lanes[0].activeControllerId, 'velocity')
  assert.deepEqual(lanes[0].controllerIds, [
    'velocity',
    'cc:1',
    'pitch_bend',
    'after_touch',
  ])
})

test('controllerDefinitionFromId_withCustomCc_returnsBoundedControllerDefinition', () => {
  const definition = controllerDefinitionFromId('cc:74')

  assert.equal(definition.id, 'cc:74')
  assert.equal(definition.type, 'control_change')
  assert.equal(definition.controller, 74)
  assert.equal(definition.label, 'CC74')
  assert.equal(definition.min, 0)
  assert.equal(definition.max, 127)
})

test('velocityDisplay_usesZeroToOneHundredLaneRange', () => {
  const definition = controllerDefinitionFromId('velocity')

  assert.deepEqual(controllerDisplayRange(definition), {
    min: 0,
    middle: 50,
    max: 100,
  })
  assert.equal(controllerDisplayValue(definition, 127), 100)
  assert.equal(controllerValueToUnit(definition, 80), 0.8)
  assert.equal(controllerUnitToValue(definition, 1), 100)
})

test('controllerLaneStackHeight_growsWithLanesWithoutInnerVerticalScroll', () => {
  assert.equal(controllerLaneStackHeight(1, 96, 28), 124)
  assert.equal(controllerLaneStackHeight(3, 96, 28), 316)
  assert.equal(controllerLaneStackHeight(0, 96, 28), 124)
})

test('controllerLaneColorStyles_useTrackColorForVelocityAndEvents', () => {
  assert.deepEqual(controllerLaneColorStyles('#12abef'), {
    velocityStroke: 'rgba(18, 171, 239, 0.92)',
    velocityFill: 'rgba(18, 171, 239, 1)',
    selectedVelocityStroke: '#f0d17a',
    selectedVelocityFill: '#ffe6a3',
    eventStroke: 'rgba(18, 171, 239, 0.78)',
    eventFill: 'rgba(18, 171, 239, 0.92)',
    eventPointStroke: 'rgba(0,0,0,0.38)',
  })
  assert.equal(controllerLaneColorStyles('bad-color').velocityFill, 'rgba(78, 121, 255, 1)')
})

test('controllerCanvas_isLayeredInsideEachLane', () => {
  const source = readFileSync(new URL('./MusicStudio.vue', import.meta.url), 'utf8')
  const match = /\.controller-canvas\s*\{(?<body>[^}]+)\}/.exec(source)

  assert.ok(match, 'controller canvas style should exist')
  assert.match(match.groups.body, /position:\s*absolute;/)
  assert.match(match.groups.body, /inset:\s*0;/)
})

test('normalizeControllerEvent_forCcClampsAndSnapsValue', () => {
  const definition = controllerDefinitionFromId('cc:74')
  const event = normalizeControllerEvent(definition, {
    id: 'drawn',
    start: 1.31,
    value: 200,
    curve_amount: 2,
  }, 0.25)

  assert.deepEqual(event, {
    id: 'drawn',
    type: 'control_change',
    start: 1.25,
    channel: 0,
    controller: 74,
    value: 127,
    curve_amount: 1,
  })
  assert.equal(eventMatchesController(event, definition), true)
})

test('normalizeControllerEvent_withQuantizeOffPreservesEventBeat', () => {
  const definition = controllerDefinitionFromId('cc:74')
  const event = normalizeControllerEvent(definition, {
    id: 'drawn',
    start: 1.31,
    value: 64,
  }, null)

  assert.equal(event.start, 1.31)
})

test('controllerRenderPoints_extendsFirstAndLastEventValuesAcrossEmptyAreas', () => {
  const definition = controllerDefinitionFromId('cc:1')
  const points = controllerRenderPoints([
    { id: 'a', type: 'control_change', controller: 1, start: 1, value: 64 },
    { id: 'b', type: 'control_change', controller: 1, start: 2, value: 32 },
  ], definition, 4)

  assert.deepEqual(points.map(point => ({
    start: point.start,
    value: point.value,
    synthetic: point.synthetic,
  })), [
    { start: 0, value: 64, synthetic: true },
    { start: 1, value: 64, synthetic: false },
    { start: 2, value: 32, synthetic: false },
    { start: 4, value: 32, synthetic: true },
  ])
})

test('controllerCurveValueAtBeat_usesCurveAmountAsMidpointOffset', () => {
  const definition = controllerDefinitionFromId('cc:1')
  const left = {
    id: 'a',
    type: 'control_change',
    controller: 1,
    start: 0,
    value: 0,
    curve_amount: 0.25,
  }
  const right = {
    id: 'b',
    type: 'control_change',
    controller: 1,
    start: 1,
    value: 127,
  }

  assert.equal(controllerCurveValueAtBeat(left, right, 0.5, definition), 95)
  assert.equal(
    controllerCurveValueAtBeat({ ...left, curve_amount: -0.25 }, right, 0.5, definition),
    32
  )
  assert.equal(controllerCurveValueAtBeat({ ...left, curve_amount: 0 }, right, 0.5, definition), 64)
})

test('applyCurveAmount_preservesUnsnappedTimeAndOnlyChangesCurveAmount', () => {
  const event = {
    id: 'drawn',
    type: 'control_change',
    start: 1.31,
    channel: 0,
    controller: 74,
    value: 64,
  }
  const point = {
    id: 'auto',
    beat: 2.37,
    value: 0.45,
    curve: 'linear',
  }

  assert.deepEqual(applyCurveAmount(event, 2), {
    ...event,
    curve_amount: 1,
  })
  assert.deepEqual(applyCurveAmount({ ...point, curve_amount: 0.5 }, 0), point)
})

test('normalizeControllerEvent_forPitchBendAndAfterTouchUseHostFields', () => {
  const bend = normalizeControllerEvent(controllerDefinitionFromId('pitch_bend'), {
    id: 'bend',
    start: 2,
    value: -9000,
  }, 0.25)
  const pressure = normalizeControllerEvent(controllerDefinitionFromId('after_touch'), {
    id: 'pressure',
    start: 3,
    value: 88,
  }, 0.25)

  assert.deepEqual(bend, {
    id: 'bend',
    type: 'pitch_bend',
    start: 2,
    channel: 0,
    value: -8192,
  })
  assert.deepEqual(pressure, {
    id: 'pressure',
    type: 'channel_pressure',
    start: 3,
    channel: 0,
    pressure: 88,
  })
})
