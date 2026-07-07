import { ref, shallowRef, triggerRef, unref, watch } from 'vue'

const instances = new Map()
const sessionObjectIds = new WeakMap()
let nextSessionObjectId = 1
const INITIAL_RECONNECT_DELAY_MS = 1000
const MAX_RECONNECT_DELAY_MS = 30000
const NON_RETRYABLE_CLOSE_CODES = new Set([
  1000, // normal closure
  1008, // policy violation, including auth/setup failures from the server
])

function sessionCacheKey(sessionId) {
  if ((typeof sessionId === 'object' || typeof sessionId === 'function') && sessionId !== null) {
    let id = sessionObjectIds.get(sessionId)
    if (!id) {
      id = nextSessionObjectId
      nextSessionObjectId += 1
      sessionObjectIds.set(sessionId, id)
    }
    return `object:${id}`
  }
  return `value:${String(sessionId ?? '')}`
}

function socketCacheKey(sessionId, options) {
  return `${String(options?.surface || '')}:${sessionCacheKey(sessionId)}`
}

export function useWebSocket(sessionId, options = {}) {
  const cacheKey = socketCacheKey(sessionId, options)
  const cached = instances.get(cacheKey)
  if (cached) return cached

  const connected = ref(false)
  const events = shallowRef([])
  const lastError = shallowRef(null)
  const lastClose = shallowRef(null)
  const reconnectDelayMs = ref(0)
  const surfaceKey = String(options.surface || '')
  let ws = null
  let reconnectTimer = null
  let reconnectAttempts = 0
  const openedOnce = ref(false)
  let active = true
  const lastRuntimeSeqBySession = {}

  function currentSessionKey() {
    return unref(sessionId) || ''
  }

  function rememberRuntimeSeq(msg, key) {
    const seq = Number(msg.runtime_seq || 0)
    if (!key || !Number.isFinite(seq) || seq <= 0) return
    lastRuntimeSeqBySession[key] = Math.max(lastRuntimeSeqBySession[key] || 0, seq)
  }

  function requestRuntimeReplay() {
    const key = currentSessionKey()
    const sinceSeq = lastRuntimeSeqBySession[key] || 0
    if (!key || sinceSeq <= 0 || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({
      type: 'runtime_replay',
      session_id: key,
      since_seq: sinceSeq,
    }))
  }

  function isBrowserOnline() {
    return typeof navigator === 'undefined' || navigator.onLine !== false
  }

  function clearReconnectTimer() {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  function nextReconnectDelay() {
    const exponent = Math.min(reconnectAttempts, 10)
    const delay = Math.min(
      INITIAL_RECONNECT_DELAY_MS * (2 ** exponent),
      MAX_RECONNECT_DELAY_MS,
    )
    reconnectAttempts += 1
    return delay
  }

  function shouldReconnect(closeEvent) {
    const code = Number(closeEvent?.code || 0)
    return !NON_RETRYABLE_CLOSE_CODES.has(code)
  }

  function scheduleReconnect() {
    clearReconnectTimer()
    if (!active || !isBrowserOnline()) {
      reconnectDelayMs.value = 0
      return
    }
    const delay = nextReconnectDelay()
    reconnectDelayMs.value = delay
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function closeCurrentSocket() {
    const socket = ws
    ws = null
    connected.value = false
    if (!socket) return

    socket.onopen = null
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    if (
      socket.readyState !== WebSocket.CLOSING
      && socket.readyState !== WebSocket.CLOSED
    ) {
      socket.close()
    }
  }

  function connect() {
    if (!active) return
    clearReconnectTimer()
    if (!isBrowserOnline()) {
      connected.value = false
      reconnectDelayMs.value = 0
      return
    }

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const surface = surfaceKey ? `?surface=${encodeURIComponent(surfaceKey)}` : ''
    const socket = new WebSocket(`${protocol}://${location.host}/ws${surface}`)
    ws = socket

    socket.onopen = () => {
      if (socket !== ws || !active) return
      connected.value = true
      lastClose.value = null
      reconnectAttempts = 0
      reconnectDelayMs.value = 0
      if (openedOnce.value) requestRuntimeReplay()
      openedOnce.value = true
    }

    socket.onmessage = (e) => {
      if (socket !== ws || !active) return
      try {
        const msg = JSON.parse(e.data)
        const currentSessionId = unref(sessionId)
        if (
          !msg.session_id ||
          !currentSessionId ||
          msg.session_id.includes(currentSessionId) ||
          currentSessionId.includes(msg.session_id)
        ) {
          rememberRuntimeSeq(msg, currentSessionId)
          events.value.push(msg)
          triggerRef(events)
        }
      } catch {}
    }

    socket.onclose = (event) => {
      if (socket !== ws || !active) return
      ws = null
      connected.value = false
      lastClose.value = event || null
      if (shouldReconnect(event)) {
        scheduleReconnect()
      } else {
        reconnectDelayMs.value = 0
      }
    }

    socket.onerror = (event) => {
      if (socket !== ws || !active) return
      connected.value = false
      lastError.value = event || null
      if (typeof options.onError === 'function') {
        options.onError(event)
      }
    }
  }

  function reconnectForSessionChange() {
    reconnectAttempts = 0
    reconnectDelayMs.value = 0
    clearReconnectTimer()
    closeCurrentSocket()
    connect()
  }

  // Manual reconnect — used by the connection banner's "Reconnect" button.
  // Resets the backoff so the user gets an immediate reconnect attempt instead
  // of waiting out the current exponential delay.
  function reconnectNow() {
    if (!active) return
    reconnectAttempts = 0
    reconnectDelayMs.value = 0
    clearReconnectTimer()
    closeCurrentSocket()
    connect()
  }

  function handleOnline() {
    if (!active || connected.value || ws || reconnectTimer) return
    reconnectAttempts = 0
    connect()
  }

  function handleOffline() {
    clearReconnectTimer()
    reconnectDelayMs.value = 0
    connected.value = false
  }

  const stopSessionWatch = watch(
    () => currentSessionKey(),
    (nextKey, previousKey) => {
      if (nextKey === previousKey) return
      reconnectForSessionChange()
    },
  )

  if (typeof window !== 'undefined') {
    window.addEventListener?.('online', handleOnline)
    window.addEventListener?.('offline', handleOffline)
  }

  function cleanup() {
    active = false
    stopSessionWatch()
    clearReconnectTimer()
    if (typeof window !== 'undefined') {
      window.removeEventListener?.('online', handleOnline)
      window.removeEventListener?.('offline', handleOffline)
    }
    closeCurrentSocket()
    instances.delete(cacheKey)
  }

  connect()

  const instance = {
    connected,
    openedOnce,
    events,
    lastError,
    lastClose,
    reconnectDelayMs,
    reconnectNow,
    cleanup,
  }
  instances.set(cacheKey, instance)

  return instance
}

export function clearWsInstance() {
  for (const instance of [...instances.values()]) {
    instance.cleanup()
  }
  instances.clear()
}
