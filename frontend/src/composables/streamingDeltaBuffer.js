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

export function createStreamingDeltaBuffer({
  apply,
  requestFrame = defaultRequestFrame,
  cancelFrame = defaultCancelFrame,
}) {
  let pending = ''
  let frameId = null
  let cancelled = false

  function clearScheduledFrame() {
    if (frameId !== null) {
      cancelFrame(frameId)
      frameId = null
    }
  }

  function flush() {
    clearScheduledFrame()
    if (cancelled || !pending) return
    const delta = pending
    pending = ''
    apply(delta)
  }

  return {
    append(delta) {
      if (cancelled || !delta) return
      pending += String(delta)
      if (frameId === null) {
        frameId = requestFrame(flush)
      }
    },
    flush,
    clear() {
      pending = ''
      clearScheduledFrame()
    },
    cancel() {
      cancelled = true
      pending = ''
      clearScheduledFrame()
    },
  }
}
