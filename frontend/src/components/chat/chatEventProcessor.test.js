import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { createChatEventProcessor } from './chatEventProcessor.js'

test('createChatEventProcessor_batchesPendingEventsIntoOneFrame', async () => {
  const frames = []
  const events = ref([
    { type: 'response_delta', content: 'a' },
    { type: 'mode_changed', mode: 'plan' },
  ])
  const handled = []
  const modes = []
  let scrollCount = 0
  const processor = createChatEventProcessor({
    events,
    handleEvent: (event) => handled.push(event.type),
    handleModeChanged: (mode) => modes.push(mode),
    scrollToBottom: () => { scrollCount += 1 },
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()
  processor.schedule()

  assert.equal(frames.length, 1)
  assert.deepEqual(handled, [])

  await frames[0]()

  assert.deepEqual(handled, ['response_delta', 'mode_changed'])
  assert.deepEqual(modes, ['plan'])
  assert.equal(scrollCount, 1)
})

test('createChatEventProcessor_resetToEndSkipsExistingEvents', async () => {
  const frames = []
  const events = ref([{ type: 'old' }])
  const handled = []
  const processor = createChatEventProcessor({
    events,
    handleEvent: (event) => handled.push(event.type),
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.resetToEnd()
  events.value.push({ type: 'new' })
  processor.schedule()
  await frames[0]()

  assert.deepEqual(handled, ['new'])
})

test('createChatEventProcessor_waitsForAsyncHandlersBeforeScrolling', async () => {
  const frames = []
  const events = ref([{ type: 'music_project' }])
  const handled = []
  let scrollCount = 0
  const processor = createChatEventProcessor({
    events,
    handleEvent: async (event) => {
      await Promise.resolve()
      handled.push(event.type)
    },
    scrollToBottom: () => { scrollCount += 1 },
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()
  const flush = frames[0]()

  assert.deepEqual(handled, [])
  assert.equal(scrollCount, 0)

  await flush

  assert.deepEqual(handled, ['music_project'])
  assert.equal(scrollCount, 1)
})

test('createChatEventProcessor_keepsSyncHandlersInOneTurn', async () => {
  const frames = []
  const events = ref([
    { type: 'tool_start', id: 't1' },
    { type: 'tool_start', id: 't2' },
    { type: 'tool_start', id: 't3' },
  ])
  const handled = []
  let sawMicrotaskGap = false
  const processor = createChatEventProcessor({
    events,
    handleEvent: (event) => {
      handled.push(event.id)
      queueMicrotask(() => {
        if (handled.length < events.value.length) sawMicrotaskGap = true
      })
    },
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()
  await frames[0]()
  await Promise.resolve()

  assert.deepEqual(handled, ['t1', 't2', 't3'])
  assert.equal(sawMicrotaskGap, false)
})

test('createChatEventProcessor_preservesMixedSyncAsyncOrder', async () => {
  const frames = []
  const events = ref([
    { type: 'tool_start', id: 'before' },
    { type: 'tool_start', id: 'async' },
    { type: 'tool_start', id: 'after' },
  ])
  const order = []
  const processor = createChatEventProcessor({
    events,
    handleEvent: (event) => {
      order.push(`${event.id}:start`)
      if (event.id === 'async') {
        return Promise.resolve().then(() => order.push(`${event.id}:end`))
      }
      order.push(`${event.id}:end`)
      return undefined
    },
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()
  await frames[0]()

  assert.deepEqual(order, [
    'before:start',
    'before:end',
    'async:start',
    'async:end',
    'after:start',
    'after:end',
  ])
})

test('createChatEventProcessor_propagatesAsyncHandlerRejections', async () => {
  const frames = []
  const events = ref([{ type: 'tool_start', id: 'failed' }])
  const expected = new Error('handler failed')
  let scrollCount = 0
  const processor = createChatEventProcessor({
    events,
    handleEvent: () => Promise.reject(expected),
    scrollToBottom: () => { scrollCount += 1 },
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()

  await assert.rejects(frames[0](), expected)
  assert.equal(scrollCount, 0)
})

test('createChatEventProcessor forwards research lifecycle events in order', async () => {
  const frames = []
  const events = ref([
    { type: 'research_started' },
    { type: 'research_phase', phase: 'gathering' },
    { type: 'research_completed' },
  ])
  const handled = []
  const processor = createChatEventProcessor({
    events,
    handleEvent: (event) => handled.push(event.type),
    requestFrame(callback) {
      frames.push(callback)
      return frames.length
    },
  })

  processor.schedule()
  await frames[0]()

  assert.deepEqual(handled, [
    'research_started',
    'research_phase',
    'research_completed',
  ])
})
