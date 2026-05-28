import assert from 'node:assert/strict'

import { normalizeAssistantChain } from './chatAssistantChain.js'

const parsed = normalizeAssistantChain(
  [
    { type: 'plain', text: 'Rendered text' },
    {
      type: 'image',
      url: 'data:image/png;base64,aGVsbG8=',
      file: 'render.png',
      mime_type: 'image/png',
      size: 5,
    },
  ],
  'fallback',
  () => 'attachment-id',
)

assert.equal(parsed.text, 'Rendered text')
assert.deepEqual(parsed.attachments, [
  {
    id: 'attachment-id',
    name: 'render.png',
    type: 'image/png',
    size: 5,
    src: 'data:image/png;base64,aGVsbG8=',
  },
])

assert.deepEqual(normalizeAssistantChain(null, 'fallback'), {
  text: 'fallback',
  attachments: [],
})
