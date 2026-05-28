import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

assert.match(source, /clearChatInstance\(\)/)
assert.match(source, /watch\(\s*isDawAgentSurface/)
assert.match(source, /addEventListener\('popstate'/)
assert.match(source, /removeEventListener\('popstate'/)
