import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { createChatRetryState } from './chatRetryState.js'

function createHarness() {
  const messages = []
  const added = []
  let sending = false
  let clearCount = 0
  const retryState = createChatRetryState({
    getMessages: () => messages,
    isSending: () => sending,
    clearErrors: () => {
      clearCount += 1
    },
    addUserMessage: (payload) => {
      const message = {
        id: `user-${added.length + 1}`,
        role: 'user',
        content: payload.text,
        attachments: payload.attachments || [],
      }
      messages.push(message)
      added.push(payload)
      return message
    },
  })

  return {
    messages,
    added,
    get clearCount() {
      return clearCount
    },
    setSending(value) {
      sending = value
    },
    retryState,
  }
}

{
  const harness = createHarness()
  const payload = {
    text: '',
    attachments: [{ name: 'take.wav' }],
    request: { prompt: '', files: ['take.wav'] },
  }

  assert.equal(harness.retryState.canRetry(), false)

  const freshPayload = harness.retryState.beginFreshSend(payload)

  assert.equal(freshPayload, payload)
  assert.equal(harness.retryState.canRetry(), true)
  assert.equal(harness.clearCount, 1)
  assert.equal(harness.messages.length, 1)
  assert.equal(harness.messages[0].id, 'user-1')
  assert.equal(harness.messages[0].attachments.length, 1)
  assert.equal(harness.retryState.getLastPayload(), payload)
}

{
  const harness = createHarness()
  const payload = { text: '', attachments: [{ name: 'mix.png' }] }
  harness.retryState.beginFreshSend(payload)

  const retryPayload = harness.retryState.beginRetry()

  assert.equal(retryPayload, payload)
  assert.equal(harness.clearCount, 2)
  assert.equal(harness.messages.length, 1)
  assert.equal(harness.added.length, 1)
}

{
  const harness = createHarness()
  const payload = { text: 'restore this turn', attachments: [] }
  harness.retryState.beginFreshSend(payload)
  harness.messages.length = 0

  const retryPayload = harness.retryState.beginRetry()

  assert.equal(retryPayload, payload)
  assert.equal(harness.messages.length, 1)
  assert.equal(harness.messages[0].id, 'user-2')
  assert.equal(harness.added.length, 2)
}

{
  const harness = createHarness()
  const payload = { text: 'busy', attachments: [] }
  harness.retryState.beginFreshSend(payload)
  harness.setSending(true)

  assert.equal(harness.retryState.canRetry(), false)
  assert.equal(harness.retryState.beginRetry(), null)
  assert.equal(harness.clearCount, 1)
  assert.equal(harness.added.length, 1)
}

{
  const harness = createHarness()
  const payload = { text: 'reset', attachments: [] }
  harness.retryState.beginFreshSend(payload)
  harness.retryState.reset()

  assert.equal(harness.retryState.canRetry(), false)
  assert.equal(harness.retryState.getLastPayload(), null)
}

{
  const here = dirname(fileURLToPath(import.meta.url))
  const useChatSource = readFileSync(resolve(here, './useChat.js'), 'utf8')
  const dawAgentSource = readFileSync(resolve(here, '../components/chat/DawAgentPage.vue'), 'utf8')

  assert.match(useChatSource, /createChatRetryState/)
  assert.match(dawAgentSource, /createChatRetryState/)
  assert.equal(dawAgentSource.includes('lastDawSendPayload'), false)
}
