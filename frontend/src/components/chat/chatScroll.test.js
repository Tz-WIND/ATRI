import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveAutoScroll, stickChatToBottom } from './chatScroll.js'

test('stickChatToBottom moves the viewport to the calculated bottom', () => {
  const element = { scrollHeight: 620, clientHeight: 200, scrollTop: 100 }

  assert.equal(stickChatToBottom(element), true)
  assert.equal(element.scrollTop, 420)
})

test('stickChatToBottom avoids a redundant write near the bottom', () => {
  let writes = 0
  const element = {
    scrollHeight: 620,
    clientHeight: 200,
    get scrollTop() { return 419 },
    set scrollTop(value) {
      writes += 1
      assert.equal(typeof value, 'number')
    },
  }

  assert.equal(stickChatToBottom(element), false)
  assert.equal(writes, 0)
})

test('stickChatToBottom respects a disabled auto-scroll gate', () => {
  const element = { scrollHeight: 620, clientHeight: 200, scrollTop: 100 }

  assert.equal(stickChatToBottom(element, false), false)
  assert.equal(element.scrollTop, 100)
})

test('manual scroll during correction disables the scheduled bottom pin', () => {
  const element = { scrollHeight: 620, clientHeight: 200, scrollTop: 200 }

  const autoScroll = resolveAutoScroll(element, true, true)

  assert.equal(autoScroll, false)
  assert.equal(stickChatToBottom(element, autoScroll), false)
  assert.equal(element.scrollTop, 200)
})
