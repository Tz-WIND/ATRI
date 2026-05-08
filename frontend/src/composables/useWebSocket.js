import { ref, unref } from 'vue'

let instance = null

export function useWebSocket(sessionId) {
  if (instance) return instance

  const connected = ref(false)
  const events = ref([])
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
    ws = new WebSocket(`${protocol}://${location.host}/ws`)

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
  }

  connect()

  instance = { connected, events, cleanup }

  return instance
}

export function clearWsInstance() {
  if (instance) {
    instance.cleanup()
    instance = null
  }
}
