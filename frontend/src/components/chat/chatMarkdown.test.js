import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getAssistantMessageCopyText,
  escapeHtml,
  highlightCode,
  shouldRenderStreamingPlainText,
} from './chatMarkdown.js'

test('shouldRenderStreamingPlainText_onlyUsesPlainTextForStreamingMarkdownAssistant', () => {
  assert.equal(shouldRenderStreamingPlainText({ role: 'assistant', md: true, streaming: true }), true)
  assert.equal(shouldRenderStreamingPlainText({ role: 'assistant', md: true, streaming: false }), false)
  assert.equal(shouldRenderStreamingPlainText({ role: 'user', md: true, streaming: true }), false)
})

test('highlightCode_escapesUnknownLanguageWithoutAutoHighlighting', () => {
  let autoHighlightCalls = 0
  const fakeHljs = {
    getLanguage: () => false,
    highlightAuto() {
      autoHighlightCalls += 1
      return { value: 'auto' }
    },
  }

  assert.equal(highlightCode('<x>', 'not-real', fakeHljs), '&lt;x&gt;')
  assert.equal(autoHighlightCalls, 0)
})

test('escapeHtml_escapesTextForPlainRendering', () => {
  assert.equal(escapeHtml('<script>"x"</script>'), '&lt;script&gt;&quot;x&quot;&lt;/script&gt;')
})

test('getAssistantMessageCopyText_returnsOriginalCompletedAssistantContent', () => {
  const original = 'Here is **markdown**.\n\n```js\nconst value = 1\n```'

  assert.equal(
    getAssistantMessageCopyText({ role: 'assistant', md: true, streaming: false, content: original }),
    original,
  )
  assert.equal(getAssistantMessageCopyText({ role: 'assistant', streaming: true, content: original }), '')
  assert.equal(getAssistantMessageCopyText({ role: 'user', content: original }), '')
})
