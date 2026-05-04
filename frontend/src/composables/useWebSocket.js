import { ref, unref } from 'vue'

let instance = null

export function useWebSocket(sessionId) {
  if (instance) return instance

  const connected = ref(false)
  const events = ref([])
  let ws = null
  let reconnectTimer = null

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${protocol}://${location.host}/ws`)

    ws.onopen = () => {
      connected.value = true
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
