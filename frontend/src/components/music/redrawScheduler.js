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

export function createPlaybackResumeController({
  refreshStatus,
  shouldResume,
  align,
  redraw,
  start,
} = {}) {
  let pending = false
  let pendingPromise = null
  let resumeRequested = false

  function runResume() {
    pending = true
    pendingPromise = (async () => {
      try {
        await refreshStatus()
        if (!resumeRequested || !shouldResume()) return
        align()
        redraw()
      } finally {
        pending = false
        pendingPromise = null
      }
      if (resumeRequested && shouldResume()) start()
    })()
    return pendingPromise
  }

  return {
    get pending() {
      return pending
    },
    resume() {
      resumeRequested = true
      return pendingPromise || runResume()
    },
    suspend() {
      resumeRequested = false
    },
  }
}

export function createPlaybackRedrawLoop({
  shouldTick,
  onTick,
  requestFrame = defaultRequestFrame,
  cancelFrame = defaultCancelFrame,
} = {}) {
  let frameId = null
  let lastTime = 0
  let stopped = true

  function loop(now) {
    frameId = null
    if (stopped) return
    if (!shouldTick()) {
      lastTime = 0
      return
    }
    const delta = lastTime ? (now - lastTime) / 1000 : 0
    lastTime = now
    onTick(delta, now)
    if (!stopped) frameId = requestFrame(loop)
  }

  return {
    start() {
      if (stopped) {
        stopped = false
      }
      if (frameId == null) frameId = requestFrame(loop)
    },
    stop() {
      stopped = true
      lastTime = 0
      if (frameId != null) {
        cancelFrame(frameId)
        frameId = null
      }
    },
  }
}
