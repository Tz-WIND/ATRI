import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  interpolateControllerValue,
  quantizeStepFromId,
  quantizedBeatsBetween,
  snapBeatToGrid,
} from './pianoQuantize.js'

test('quantizeStepFromId_returnsOptionalPianoGridSteps', () => {
  assert.equal(quantizeStepFromId('off'), null)
  assert.equal(quantizeStepFromId('1/16'), 0.25)
  assert.equal(quantizeStepFromId('missing'), 0.25)
})

test('snapBeatToGrid_preservesRawBeatWhenQuantizeIsOff', () => {
  assert.equal(snapBeatToGrid(1.31, null), 1.31)
  assert.equal(snapBeatToGrid(1.31, 0.25), 1.25)
})

test('quantizedBeatsBetween_emitsEveryGridPointCrossedByDrag', () => {
  assert.deepEqual(quantizedBeatsBetween(0.1, 0.9, 0.25), [0.25, 0.5, 0.75, 1])
  assert.deepEqual(quantizedBeatsBetween(0.9, 0.1, 0.25), [0.75, 0.5, 0.25, 0])
  assert.deepEqual(quantizedBeatsBetween(0.1, 0.9, null), [0.9])
})

test('interpolateControllerValue_returnsValueAtQuantizedBeat', () => {
  assert.equal(interpolateControllerValue(0, 0, 1, 100, 0.5), 50)
  assert.equal(interpolateControllerValue(0.5, 80, 0.5, 10, 0.5), 10)
})

test('pianoQuantizeControl_usesCustomDarkMenuInsteadOfNativeSelect', () => {
  const source = readFileSync(new URL('./MusicStudio.vue', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /<select\s+v-model="pianoQuantizeId"/)
  assert.match(source, /class="piano-quantize-menu"/)
})
