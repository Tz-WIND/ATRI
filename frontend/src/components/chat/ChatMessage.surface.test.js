import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatMessage.vue', import.meta.url), 'utf8')

assert.equal(source.includes('class="assistant-copy-action"'), false)
assert.equal(source.includes('bottom: -26px'), false)
assert.equal(source.includes('user-action'), false)
assert.equal(
  /class="markdown-body"[\s\S]*v-html="renderedContent"[\s\S]*<span\s+v-if="message\.streaming"\s+class="stream-cursor"/.test(source),
  false,
)
