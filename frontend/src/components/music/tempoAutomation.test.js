import assert from 'node:assert/strict'
import test from 'node:test'

import {
  beatsToSeconds,
  effectiveTempoAtBeat,
  secondsToBeats,
} from './tempoAutomation.js'

test('effectiveTempoAtBeat_usesGlobalTempoAutomationAsHeldPoints', () => {
  const project = {
    tempo: 120,
    tracks: [
      {
        type: 'automation',
        target: { kind: 'tempo_bpm' },
        automation: {
          points: [
            { beat: 4, value: 90 },
            { beat: 8, value: 60 },
          ],
        },
      },
    ],
  }

  assert.equal(effectiveTempoAtBeat(project, 2), 120)
  assert.equal(effectiveTempoAtBeat(project, 4), 90)
  assert.equal(effectiveTempoAtBeat(project, 12), 60)
})

test('beatsToSeconds_integratesTempoAutomationSegments', () => {
  const project = {
    tempo: 120,
    tracks: [
      {
        type: 'automation',
        target: { kind: 'tempo_bpm' },
        automation: {
          points: [
            { beat: 4, value: 60 },
            { beat: 8, value: 180 },
          ],
        },
      },
    ],
  }

  assert.equal(beatsToSeconds(project, 4), 2)
  assert.equal(beatsToSeconds(project, 8), 6)
  assert.equal(beatsToSeconds(project, 11), 7)
})

test('secondsToBeats_invertsTempoAutomationSegments', () => {
  const project = {
    tempo: 120,
    tracks: [
      {
        type: 'automation',
        target: { kind: 'tempo_bpm' },
        automation: {
          points: [
            { beat: 4, value: 60 },
            { beat: 8, value: 180 },
          ],
        },
      },
    ],
  }

  assert.equal(secondsToBeats(project, 2), 4)
  assert.equal(secondsToBeats(project, 6), 8)
  assert.equal(secondsToBeats(project, 7), 11)
})
