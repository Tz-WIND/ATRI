import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ConnectionBanner.vue', import.meta.url), 'utf8')
const chatPageSource = readFileSync(new URL('./ChatPage.vue', import.meta.url), 'utf8')
const dawAgentPageSource = readFileSync(new URL('./DawAgentPage.vue', import.meta.url), 'utf8')

assert.match(source, /import \{[^}]*onUnmounted[^}]*\} from 'vue'/)
assert.match(source, /onUnmounted\(\s*clearTick\s*\)/)
assert.match(source, /openedOnce:\s*\{\s*type:\s*Boolean,\s*default:\s*false\s*\}/)
assert.match(source, /const visible = computed\(\(\) => props\.openedOnce && !props\.connected\)/)
assert.match(chatPageSource, /<ConnectionBanner[\s\S]*:opened-once="wsOpenedOnce"[\s\S]*:connected="wsConnected"/)
assert.match(dawAgentPageSource, /<ConnectionBanner[\s\S]*:opened-once="wsOpenedOnce"[\s\S]*:connected="wsConnected"/)
