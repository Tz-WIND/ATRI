import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatMessage.vue', import.meta.url), 'utf8')
const chatPageSource = readFileSync(new URL('./ChatPage.vue', import.meta.url), 'utf8')

assert.equal(source.includes('class="assistant-copy-action"'), false)
assert.equal(source.includes('bottom: -26px'), false)
assert.equal(source.includes('user-action'), false)
assert.match(source, /v-if="message\.role === 'assistant'"\s+class="msg-head"/)
assert.equal(/v-if="message\.role !== 'user'"\s+class="msg-head"/.test(source), false)
assert.equal(
  /class="markdown-body"[\s\S]*v-html="renderedContent"[\s\S]*<span\s+v-if="message\.streaming"\s+class="stream-cursor"/.test(source),
  false,
)
assert.match(source, /retryDisabled:\s*\{\s*type:\s*Boolean,\s*default:\s*false\s*\}/)
assert.match(source, /const retryDisabled = computed\(\(\) => retrying\.value \|\| props\.retryDisabled\)/)
assert.match(chatPageSource, /<ChatMessage[\s\S]*:retry-disabled="sending"[\s\S]*@retry="handleRetryMessage"/)
assert.equal(source.includes("from 'marked'"), false)
assert.equal(/marked\.(?:setOptions|use)\(/.test(source), false)
assert.match(source, /renderChatMarkdown/)
assert.match(source, /import \{[^}]*onUnmounted[^}]*\} from 'vue'/)
assert.match(source, /const markdownCopyResetTimers = new Map\(\)/)
assert.match(source, /function clearCopyTimers\(\)/)
assert.match(source, /function clearMessageTimers\(\)/)
assert.match(source, /window\.clearTimeout/)
assert.match(source, /onUnmounted\(\s*clearMessageTimers\s*\)/)
