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
