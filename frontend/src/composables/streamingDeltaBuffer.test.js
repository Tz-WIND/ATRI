import assert from 'node:assert/strict'
import test from 'node:test'

import { createStreamingDeltaBuffer } from './streamingDeltaBuffer.js'

test('createStreamingDeltaBuffer_coalescesDeltasUntilScheduledFlush', () => {
  const frames = []
  const applied = []
  const buffer = createStreamingDeltaBuffer({
    apply: (delta) => applied.push(delta),
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  buffer.append('Hel')
  buffer.append('lo')

  assert.equal(frames.length, 1)
  assert.deepEqual(applied, [])

  frames[0]()

  assert.deepEqual(applied, ['Hello'])
})

test('createStreamingDeltaBuffer_cancelDropsPendingDelta', () => {
  const frames = []
  const cancelled = []
  const applied = []
  const buffer = createStreamingDeltaBuffer({
    apply: (delta) => applied.push(delta),
    requestFrame(callback) {
      frames.push(callback)
      return 7
    },
    cancelFrame(id) {
      cancelled.push(id)
    },
  })

  buffer.append('unused')
  buffer.cancel()
  frames[0]()

  assert.deepEqual(cancelled, [7])
  assert.deepEqual(applied, [])
})

test('createStreamingDeltaBuffer_clearDropsPendingDeltaAndKeepsBufferUsable', () => {
  const frames = []
  const applied = []
  const buffer = createStreamingDeltaBuffer({
    apply: (delta) => applied.push(delta),
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  buffer.append('old')
  buffer.clear()
  buffer.append('new')

  frames[0]()
  frames[1]()

  assert.deepEqual(applied, ['new'])
})
