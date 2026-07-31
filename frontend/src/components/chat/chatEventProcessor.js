function defaultRequestFrame(callback) {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(callback)
  return setTimeout(callback, 0)
}

function defaultCancelFrame(frameId) {
  if (typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(frameId)
  } else {
    clearTimeout(frameId)
  }
}

export function createChatEventProcessor({
  events,
  handleEvent,
  handleModeChanged = () => {},
  scrollToBottom = () => {},
  requestFrame = defaultRequestFrame,
  cancelFrame = defaultCancelFrame,
}) {
  let handledEventCount = 0
  let frameId = null
  let cancelled = false
  let processing = false

  async function flush() {
    frameId = null
    if (cancelled || processing) return
    processing = true
    try {
      let handledAny = false
      while (!cancelled && handledEventCount < events.value.length) {
        const event = events.value[handledEventCount]
        handledEventCount += 1
        if (event?.type === 'mode_changed') {
          handleModeChanged(event.mode)
        }
        // Only await real promises. Awaiting a sync handler inserts a microtask
        // between events, letting Vue flush intermediate list and scroll states.
        const result = handleEvent(event)
        if (result != null && typeof result.then === 'function') {
          await result
        }
        handledAny = true
      }
      if (handledAny && !cancelled) scrollToBottom()
    } finally {
      processing = false
      if (!cancelled && handledEventCount < events.value.length) {
        processor.schedule()
      }
    }
  }

  const processor = {
    schedule() {
      if (cancelled) return
      if (processing) return
      if (frameId !== null) return
      frameId = requestFrame(flush)
    },
    resetToEnd() {
      handledEventCount = events.value.length
    },
    cancel() {
      cancelled = true
      if (frameId !== null) {
        cancelFrame(frameId)
        frameId = null
      }
    },
  }
  return processor
}
