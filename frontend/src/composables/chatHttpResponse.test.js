import assert from 'node:assert/strict'

import {
  hasActiveAssistantStream,
  hasAssistantResponse,
  shouldAppendHttpAssistantResponse,
} from './chatHttpResponse.js'

assert.equal(
  await shouldAppendHttpAssistantResponse({ value: [] }, 'pong', 0),
  true,
)

assert.equal(
  hasActiveAssistantStream({
    value: [{ role: 'assistant', content: 'po', streaming: true }],
  }),
  true,
)

assert.equal(
  await shouldAppendHttpAssistantResponse({
    value: [{ role: 'assistant', content: 'po', streaming: true }],
  }, 'pong', 0),
  false,
)

assert.equal(
  hasAssistantResponse({
    value: [{ role: 'assistant', content: 'pong', streaming: false }],
  }, 'pong'),
  true,
)

assert.equal(
  hasAssistantResponse({
    value: [{ role: 'assistant', content: 'pong' }],
  }, 'pong'),
  true,
)

assert.equal(
  await shouldAppendHttpAssistantResponse({
    value: [{ role: 'assistant', content: 'pong', streaming: false }],
  }, 'pong', 0),
  false,
)

const raceMessages = { value: [] }
const decision = shouldAppendHttpAssistantResponse(raceMessages, 'pong', 5)
setTimeout(() => {
  raceMessages.value.push({ role: 'assistant', content: 'pong', streaming: false })
}, 0)
assert.equal(await decision, false)
