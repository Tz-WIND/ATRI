import assert from 'node:assert/strict'

import { ref } from 'vue'
import { clearWsInstance, useWebSocket } from './useWebSocket.js'

const originalLocation = globalThis.location
const originalWebSocket = globalThis.WebSocket
const sockets = []

class FakeWebSocket {
  static OPEN = 1

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.OPEN
    this.sent = []
    sockets.push(this)
  }

  send(payload) {
    this.sent.push(payload)
  }

  close() {
    this.readyState = 3
  }
}

globalThis.location = { protocol: 'http:', host: '127.0.0.1:6185' }
globalThis.WebSocket = FakeWebSocket

try {
  clearWsInstance()

  const chatSession = ref('webchat:friend:default')
  const dawSession = ref('daw_agent:friend:song-a')
  const otherChatSession = ref('webchat:friend:other')
  const chat = useWebSocket(chatSession)
  const sameChat = useWebSocket(chatSession)
  const daw = useWebSocket(dawSession, { surface: 'daw-agent' })
  const otherChat = useWebSocket(otherChatSession)

  assert.equal(chat, sameChat)
  assert.notEqual(chat, daw)
  assert.notEqual(chat, otherChat)
  assert.equal(sockets.length, 3)
  assert.equal(sockets[0].url, 'ws://127.0.0.1:6185/ws')
  assert.equal(sockets[1].url, 'ws://127.0.0.1:6185/ws?surface=daw-agent')
  assert.equal(sockets[2].url, 'ws://127.0.0.1:6185/ws')

  sockets[0].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'webchat:friend:default' }),
  })
  sockets[1].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'daw_agent:friend:song-a' }),
  })
  sockets[2].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'webchat:friend:other' }),
  })

  assert.equal(chat.events.value.length, 1)
  assert.equal(daw.events.value.length, 1)
  assert.equal(otherChat.events.value.length, 1)
  assert.equal(chat.events.value[0].session_id, 'webchat:friend:default')
  assert.equal(daw.events.value[0].session_id, 'daw_agent:friend:song-a')
  assert.equal(otherChat.events.value[0].session_id, 'webchat:friend:other')

  clearWsInstance()
  assert.equal(sockets[0].readyState, 3)
  assert.equal(sockets[1].readyState, 3)
  assert.equal(sockets[2].readyState, 3)
} finally {
  clearWsInstance()
  globalThis.location = originalLocation
  globalThis.WebSocket = originalWebSocket
}
