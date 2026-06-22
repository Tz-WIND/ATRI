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

export function createRafRedrawScheduler(draw, {
  requestFrame = defaultRequestFrame,
  cancelFrame = defaultCancelFrame,
} = {}) {
  let pending = false
  let frameId = null
  let cancelled = false

  function flush() {
    pending = false
    frameId = null
    if (!cancelled) draw()
  }

  return {
    request() {
      if (cancelled || pending) return
      pending = true
      frameId = requestFrame(flush)
    },
    cancel() {
      cancelled = true
      if (pending) {
        cancelFrame(frameId)
        pending = false
        frameId = null
      }
    },
  }
}
