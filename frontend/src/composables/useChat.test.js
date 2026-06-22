import assert from 'node:assert/strict'

import { clearChatInstance, useChat } from './useChat.js'

const originalLocalStorage = globalThis.localStorage
const originalFetch = globalThis.fetch
const originalRequestAnimationFrame = globalThis.requestAnimationFrame
const originalCancelAnimationFrame = globalThis.cancelAnimationFrame
const storage = new Map()
globalThis.localStorage = {
  getItem(key) {
    return storage.get(key) || null
  },
  setItem(key, value) {
    storage.set(key, String(value))
  },
  removeItem(key) {
    storage.delete(key)
  },
}

try {
  clearChatInstance()

  const first = useChat()
  first.addMessage('user', 'chat surface')

  assert.equal(useChat(), first)
  assert.equal(first.messages.value.length, 1)

  clearChatInstance()

  const second = useChat()
  assert.notEqual(second, first)
  assert.deepEqual(second.messages.value, [])

  second.loadTranscript({
    messages: [
      {
        role: 'user',
        content: 'Summarize\n\n[File: brief.docx]\nPortfolio',
        _atri_display_content: 'Summarize',
        _atri_attachments: [
          {
            kind: 'file',
            name: 'brief.docx',
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            size: 42,
          },
        ],
      },
    ],
  })

  assert.equal(second.messages.value.length, 1)
  assert.equal(second.messages.value[0].content, 'Summarize')
  assert.deepEqual(second.messages.value[0].attachments, [
    {
      id: second.messages.value[0].attachments[0].id,
      kind: 'file',
      name: 'brief.docx',
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      size: 42,
    },
  ])

  clearChatInstance()

  const streaming = useChat()
  streaming.handleWsEvent({ type: 'response_start' })
  streaming.handleWsEvent({ type: 'response_delta', content: 'Hel' })
  streaming.handleWsEvent({ type: 'response_delta', content: 'lo' })

  assert.equal(streaming.messages.value.length, 1)
  assert.equal(streaming.messages.value[0].content, '')
  assert.equal(streaming.messages.value[0].streaming, true)

  streaming.handleWsEvent({ type: 'response_done' })

  assert.equal(streaming.messages.value[0].content, 'Hello')
  assert.equal(streaming.messages.value[0].streaming, false)

  clearChatInstance()

  const frames = []
  const cancelledFrames = new Set()
  globalThis.requestAnimationFrame = (callback) => {
    const id = frames.length + 1
    frames.push({ id, callback })
    return id
  }
  globalThis.cancelAnimationFrame = (id) => {
    cancelledFrames.add(id)
  }
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({}),
  })

  const rollover = useChat()
  rollover.handleWsEvent({ type: 'response_start' })
  rollover.handleWsEvent({ type: 'response_delta', content: 'old' })

  assert.equal(frames.length, 1)

  const sendPromise = rollover.sendMessage('next turn')
  rollover.handleWsEvent({ type: 'response_start' })
  rollover.handleWsEvent({ type: 'response_delta', content: 'new' })

  for (const frame of frames) {
    if (!cancelledFrames.has(frame.id)) frame.callback()
  }

  const lastAssistant = rollover.messages.value.filter((message) => message.role === 'assistant').at(-1)
  assert.equal(lastAssistant.content, 'new')

  rollover.handleWsEvent({ type: 'response_done' })
  await sendPromise
} finally {
  clearChatInstance()
  globalThis.localStorage = originalLocalStorage
  globalThis.fetch = originalFetch
  globalThis.requestAnimationFrame = originalRequestAnimationFrame
  globalThis.cancelAnimationFrame = originalCancelAnimationFrame
}
