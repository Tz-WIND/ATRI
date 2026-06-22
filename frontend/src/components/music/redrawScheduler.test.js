import assert from 'node:assert/strict'
import test from 'node:test'

import { createRafRedrawScheduler } from './redrawScheduler.js'

test('createRafRedrawScheduler_coalescesRequestsUntilFrameFlush', () => {
  const frames = []
  let drawCount = 0
  const scheduler = createRafRedrawScheduler(
    () => { drawCount += 1 },
    {
      requestFrame(callback) {
        frames.push(callback)
        return frames.length
      },
    }
  )

  scheduler.request()
  scheduler.request()

  assert.equal(frames.length, 1)
  assert.equal(drawCount, 0)

  frames[0]()

  assert.equal(drawCount, 1)
})

test('createRafRedrawScheduler_allowsNextRequestAfterFlush', () => {
  const frames = []
  let drawCount = 0
  const scheduler = createRafRedrawScheduler(
    () => { drawCount += 1 },
    {
      requestFrame(callback) {
        frames.push(callback)
        return frames.length
      },
    }
  )

  scheduler.request()
  frames[0]()
  scheduler.request()
  frames[1]()

  assert.equal(frames.length, 2)
  assert.equal(drawCount, 2)
})

test('createRafRedrawScheduler_cancelDropsPendingDraw', () => {
  const cancelled = []
  const frames = []
  let drawCount = 0
  const scheduler = createRafRedrawScheduler(
    () => { drawCount += 1 },
    {
      requestFrame(callback) {
        frames.push(callback)
        return 42
      },
      cancelFrame(id) {
        cancelled.push(id)
      },
    }
  )

  scheduler.request()
  scheduler.cancel()
  frames[0]()

  assert.deepEqual(cancelled, [42])
  assert.equal(drawCount, 0)
})
