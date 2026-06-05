import assert from 'node:assert/strict'

import { clearChatInstance, useChat } from './useChat.js'

const originalLocalStorage = globalThis.localStorage
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
} finally {
  clearChatInstance()
  globalThis.localStorage = originalLocalStorage
}
