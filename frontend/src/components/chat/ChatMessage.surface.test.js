import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatMessage.vue', import.meta.url), 'utf8')

assert.equal(source.includes('class="assistant-copy-action"'), false)
assert.equal(source.includes('bottom: -26px'), false)
