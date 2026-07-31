import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatPage.vue', import.meta.url), 'utf8')

assert.equal(/watch\(\s*messages[\s\S]*?\{\s*deep:\s*true\s*\}/.test(source), false)
assert.match(source, /const messageScrollSignature = computed\(\(\) => \{/)
assert.match(source, /watch\(\s*messageScrollSignature,\s*\(\)\s*=>\s*scrollToBottom\(\)\s*\)/)
assert.match(source, /scroll-behavior:\s*auto/)
assert.equal(/scroll-behavior:\s*smooth/.test(source), false)
assert.match(source, /programmaticScroll/)
assert.match(
  source,
  /async function handleRetryMessage\(\)\s*\{\s*if \(!canRetry\(\)\) return\s*await retryLastMessage\(\)\s*\}/,
)
assert.match(source, /import ResearchProgress from '\.\/ResearchProgress\.vue'/)
assert.match(source, /import \{ normalizeAgentMode \} from '@\/composables\/agentMode\.js'/)
assert.match(source, /<ResearchProgress\s+:status="researchStatus"/)
