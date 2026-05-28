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
} finally {
  clearChatInstance()
  globalThis.localStorage = originalLocalStorage
}
