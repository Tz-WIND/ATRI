import assert from 'node:assert/strict'

import { nextTick, ref } from 'vue'
import { clearWsInstance, useWebSocket } from './useWebSocket.js'

const originalLocation = globalThis.location
const originalWebSocket = globalThis.WebSocket
const originalSetTimeout = globalThis.setTimeout
const originalClearTimeout = globalThis.clearTimeout
const originalWindow = globalThis.window
const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
const sockets = []
const timers = []
const listeners = new Map()
let nextTimerId = 1
let online = true

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

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
    this.readyState = FakeWebSocket.CLOSED
  }
}

function pendingTimers() {
  return timers.filter((timer) => !timer.cleared)
}

function runTimer(timer) {
  if (!timer || timer.cleared) return
  timer.cleared = true
  timer.callback()
}

function resetTransportState() {
  clearWsInstance()
  sockets.length = 0
  timers.length = 0
  listeners.clear()
  nextTimerId = 1
  online = true
}

function dispatchWindowEvent(type) {
  for (const handler of listeners.get(type) || []) {
    handler({ type })
  }
}

globalThis.location = { protocol: 'http:', host: '127.0.0.1:6185' }
globalThis.WebSocket = FakeWebSocket
globalThis.setTimeout = (callback, delay) => {
  const timer = {
    id: nextTimerId,
    callback,
    delay,
    cleared: false,
  }
  nextTimerId += 1
  timers.push(timer)
  return timer.id
}
globalThis.clearTimeout = (id) => {
  const timer = timers.find((item) => item.id === id)
  if (timer) timer.cleared = true
}
globalThis.window = {
  addEventListener(type, handler) {
    const handlers = listeners.get(type) || new Set()
    handlers.add(handler)
    listeners.set(type, handlers)
  },
  removeEventListener(type, handler) {
    listeners.get(type)?.delete(handler)
  },
}
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: {
    get onLine() {
      return online
    },
  },
})

try {
  resetTransportState()

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
  const chatEventsArray = chat.events.value
  sockets[1].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'daw_agent:friend:song-a' }),
  })
  sockets[2].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'webchat:friend:other' }),
  })
  sockets[0].onmessage({
    data: JSON.stringify({ type: 'response_delta', session_id: 'webchat:friend:default' }),
  })

  assert.equal(chat.events.value, chatEventsArray)
  assert.equal(chat.events.value.length, 2)
  assert.equal(daw.events.value.length, 1)
  assert.equal(otherChat.events.value.length, 1)
  assert.equal(chat.events.value[0].session_id, 'webchat:friend:default')
  assert.equal(daw.events.value[0].session_id, 'daw_agent:friend:song-a')
  assert.equal(otherChat.events.value[0].session_id, 'webchat:friend:other')

  clearWsInstance()
  assert.equal(sockets[0].readyState, 3)
  assert.equal(sockets[1].readyState, 3)
  assert.equal(sockets[2].readyState, 3)

  resetTransportState()

  const reconnecting = useWebSocket(ref('webchat:friend:reconnect'))
  sockets[0].onopen()
  assert.equal(reconnecting.connected.value, true)

  sockets[0].onclose({ code: 1006, reason: 'abnormal closure' })

  assert.equal(reconnecting.connected.value, false)
  assert.equal(pendingTimers().length, 1)
  assert.equal(pendingTimers()[0].delay, 1000)

  runTimer(pendingTimers()[0])
  assert.equal(sockets.length, 2)

  sockets[1].onclose({ code: 1006, reason: 'abnormal closure' })

  assert.equal(pendingTimers().length, 1)
  assert.equal(pendingTimers()[0].delay, 2000)

  resetTransportState()

  const denied = useWebSocket(ref('webchat:friend:denied'))
  sockets[0].onclose({ code: 1008, reason: 'policy violation' })

  assert.equal(denied.connected.value, false)
  assert.equal(pendingTimers().length, 0)

  resetTransportState()

  const errors = []
  const erring = useWebSocket(ref('webchat:friend:error'), {
    onError: (event) => errors.push(event),
  })
  const errorEvent = { type: 'error', message: 'socket failed' }
  sockets[0].onopen()
  sockets[0].onerror(errorEvent)

  assert.equal(erring.connected.value, false)
  assert.equal(erring.lastError.value, errorEvent)
  assert.deepEqual(errors, [errorEvent])

  resetTransportState()

  const offlineSocket = useWebSocket(ref('webchat:friend:offline'))
  online = false
  sockets[0].onclose({ code: 1006, reason: 'network down' })

  assert.equal(offlineSocket.connected.value, false)
  assert.equal(pendingTimers().length, 0)

  online = true
  dispatchWindowEvent('online')

  assert.equal(sockets.length, 2)

  resetTransportState()

  const changingSession = ref('webchat:friend:first')
  useWebSocket(changingSession)
  const firstSessionSocket = sockets[0]

  changingSession.value = 'webchat:friend:second'
  await nextTick()

  assert.equal(firstSessionSocket.readyState, FakeWebSocket.CLOSED)
  assert.equal(sockets.length, 2)
  assert.equal(pendingTimers().length, 0)
} finally {
  clearWsInstance()
  globalThis.location = originalLocation
  globalThis.WebSocket = originalWebSocket
  globalThis.setTimeout = originalSetTimeout
  globalThis.clearTimeout = originalClearTimeout
  if (originalWindow === undefined) {
    delete globalThis.window
  } else {
    globalThis.window = originalWindow
  }
  if (originalNavigatorDescriptor) {
    Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
  } else {
    delete globalThis.navigator
  }
}
