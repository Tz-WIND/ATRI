import assert from 'node:assert/strict'
import test from 'node:test'

import {
  VIEWPORT_PAD_PX,
  rectIntersectsViewport,
  scrollViewport,
  shouldRunPlaybackRedraw,
  visibleBeatRange,
  visiblePitchRange,
  xIntersectsViewport,
} from './studioViewport.js'

test('scrollViewport_expandsScrollWindowByPadding', () => {
  assert.equal(VIEWPORT_PAD_PX, 64)
  assert.deepEqual(
    scrollViewport({
      scrollLeft: 200,
      scrollTop: 80,
      clientWidth: 400,
      clientHeight: 300,
      pad: 64,
    }),
    {
      xStart: 136,
      xEnd: 664,
      yStart: 16,
      yEnd: 444,
    }
  )
})

test('scrollViewport_doesNotGoNegative', () => {
  assert.deepEqual(
    scrollViewport({
      scrollLeft: 10,
      scrollTop: 4,
      clientWidth: 100,
      clientHeight: 50,
      pad: 64,
    }),
    {
      xStart: 0,
      xEnd: 174,
      yStart: 0,
      yEnd: 118,
    }
  )
})

test('rectIntersectsViewport_rejectsRectsCompletelyOutside', () => {
  const viewport = scrollViewport({
    scrollLeft: 0,
    scrollTop: 0,
    clientWidth: 200,
    clientHeight: 100,
    pad: 0,
  })

  assert.equal(
    rectIntersectsViewport({ x: 0, y: 0, w: 40, h: 12 }, viewport),
    true
  )
  assert.equal(
    rectIntersectsViewport({ x: 400, y: 0, w: 40, h: 12 }, viewport),
    false
  )
  assert.equal(
    rectIntersectsViewport({ x: 0, y: 400, w: 40, h: 12 }, viewport),
    false
  )
})

test('visiblePitchRange_returnsOnlyRowsOverlappingViewport', () => {
  const viewport = scrollViewport({
    scrollLeft: 0,
    scrollTop: 120,
    clientWidth: 200,
    clientHeight: 48,
    pad: 0,
  })
  const range = visiblePitchRange({
    viewport,
    minPitch: 0,
    maxPitch: 120,
    rowHeight: 12,
    headerHeight: 24,
  })

  assert.deepEqual(range, { minPitch: 109, maxPitch: 112 })
})

test('visibleBeatRange_mapsViewportXToLocalBeats', () => {
  const viewport = scrollViewport({
    scrollLeft: 80,
    scrollTop: 0,
    clientWidth: 40,
    clientHeight: 100,
    pad: 0,
  })
  const range = visibleBeatRange({
    viewport,
    originX: 40,
    pxPerBeat: 20,
    clipStart: 8,
  })

  assert.equal(range.firstIndex, 2)
  assert.equal(range.lastIndex, 4)
  assert.equal(range.startBeat, 10)
  assert.equal(range.endBeat, 12)
})

test('xIntersectsViewport_usedForControllerStems', () => {
  const viewport = scrollViewport({
    scrollLeft: 100,
    scrollTop: 0,
    clientWidth: 50,
    clientHeight: 20,
    pad: 0,
  })
  assert.equal(xIntersectsViewport(120, viewport), true)
  assert.equal(xIntersectsViewport(10, viewport, 4), false)
})

test('shouldRunPlaybackRedraw_stopsWhenHiddenOrIdle', () => {
  assert.equal(shouldRunPlaybackRedraw({
    playing: true,
    hidden: false,
    visualBeats: 1,
    transportBeats: 1,
  }), true)
  assert.equal(shouldRunPlaybackRedraw({
    playing: true,
    hidden: true,
    visualBeats: 1,
    transportBeats: 1,
  }), false)
  assert.equal(shouldRunPlaybackRedraw({
    playing: false,
    hidden: false,
    visualBeats: 4,
    transportBeats: 2,
  }), true)
  assert.equal(shouldRunPlaybackRedraw({
    playing: false,
    hidden: false,
    visualBeats: 2,
    transportBeats: 2,
  }), false)
})
