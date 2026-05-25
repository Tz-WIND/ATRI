import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_PIANO_FOCUS_PITCH,
  pianoFocusPitch,
  pianoScrollTopForNotes,
} from './pianoViewport.js'

const RANGE_ARGS = {
  minPitch: 0,
  maxPitch: 120,
  rowHeight: 12,
  noteTop: 24,
  clientHeight: 480,
  scrollHeight: 24 + 121 * 12,
}

test('pianoFocusPitch_usesSelectedNotesBeforeClipRange', () => {
  const notes = [
    { id: 'low', pitch: 36 },
    { id: 'selected', pitch: 72 },
    { id: 'high', pitch: 84 },
  ]

  assert.equal(pianoFocusPitch(notes, new Set(['selected'])), 72)
})

test('pianoFocusPitch_centersClipNoteRangeWhenNoSelectionMatches', () => {
  const notes = [
    { id: 'low', pitch: 48 },
    { id: 'mid', pitch: 60 },
    { id: 'high', pitch: 72 },
  ]

  assert.equal(pianoFocusPitch(notes, new Set(['missing'])), 60)
})

test('pianoScrollTopForNotes_recentersEmptyClipNearC4AfterRangeExpansion', () => {
  assert.equal(DEFAULT_PIANO_FOCUS_PITCH, 60)

  assert.equal(pianoScrollTopForNotes({ notes: [], selectedNoteIds: new Set(), ...RANGE_ARGS }), 510)
})

