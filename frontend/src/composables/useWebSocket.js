import { ref, unref } from 'vue'

const instances = new Map()
const sessionObjectIds = new WeakMap()
let nextSessionObjectId = 1

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
  const events = ref([])
  const surfaceKey = String(options.surface || '')
  let ws = null
  let reconnectTimer = null
  let openedOnce = false
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

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const surface = surfaceKey ? `?surface=${encodeURIComponent(surfaceKey)}` : ''
    ws = new WebSocket(`${protocol}://${location.host}/ws${surface}`)

    ws.onopen = () => {
      connected.value = true
      if (openedOnce) requestRuntimeReplay()
      openedOnce = true
    }

    ws.onmessage = (e) => {
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
          events.value = [...events.value, msg]
        }
      } catch {}
    }

    ws.onclose = () => {
      connected.value = false
      reconnectTimer = setTimeout(connect, 3000)
    }

    ws.onerror = () => {}
  }

  function cleanup() {
    clearTimeout(reconnectTimer)
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    instances.delete(cacheKey)
  }

  connect()

  const instance = { connected, events, cleanup }
  instances.set(cacheKey, instance)

  return instance
}

export function clearWsInstance() {
  for (const instance of [...instances.values()]) {
    instance.cleanup()
  }
  instances.clear()
}
