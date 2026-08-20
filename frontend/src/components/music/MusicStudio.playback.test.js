import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import * as redrawScheduler from './redrawScheduler.js'

const { createPlaybackRedrawLoop } = redrawScheduler

const studioSource = readFileSync(new URL('./MusicStudio.vue', import.meta.url), 'utf8')

test('musicStudio_pausesPlaybackRedrawWhenHiddenOrDeactivated', () => {
  assert.match(studioSource, /createPlaybackRedrawLoop/)
  assert.match(studioSource, /shouldRunPlaybackRedraw/)
  assert.match(studioSource, /onActivated/)
  assert.match(studioSource, /onDeactivated/)
  assert.match(studioSource, /document\.hidden/)
})

test('musicStudio_routesPlaybackResumeThroughThePendingGuard', () => {
  assert.match(studioSource, /createPlaybackResumeController/)
  assert.match(studioSource, /refreshStatus: refreshHostStatus/)
  assert.match(studioSource, /visualPositionBeats\.value = positionBeats\.value/)

  const ensureStart = studioSource.indexOf('function ensurePlaybackLoop()')
  const stopStart = studioSource.indexOf('function stopPlaybackLoop()')
  const ensureSource = studioSource.slice(ensureStart, stopStart)
  assert.match(
    ensureSource,
    /if \(playbackResumeController\?\.pending \|\| studioIsHidden\(\)\) return/
  )

  const stopEnd = studioSource.indexOf('function onVisibilityChange()')
  const stopSource = studioSource.slice(stopStart, stopEnd)
  assert.match(stopSource, /playbackResumeController\?\.suspend\(\)/)

  const visibilityMatch = studioSource.match(/function onVisibilityChange\(\) \{([\s\S]*?)\n\}/)
  assert.ok(visibilityMatch)
  assert.match(visibilityMatch[1], /resumePlaybackLoop\(\)/)

  const activatedMatch = studioSource.match(/onActivated\(\(\) => \{([\s\S]*?)\n\}\)/)
  assert.ok(activatedMatch)
  assert.match(activatedMatch[1], /resumePlaybackLoop\(\)/)
})

test('createPlaybackResumeController_blocksEarlyStartUntilRefreshAndAlignmentFinish', async () => {
  assert.equal(typeof redrawScheduler.createPlaybackResumeController, 'function')
  let finishRefresh
  const events = []
  const controller = redrawScheduler.createPlaybackResumeController({
    refreshStatus() {
      events.push('refresh')
      return new Promise(resolve => { finishRefresh = resolve })
    },
    shouldResume: () => true,
    align() { events.push('align') },
    redraw() { events.push('redraw') },
    start() { events.push('start') },
  })

  const resume = controller.resume()

  assert.equal(controller.pending, true)
  assert.deepEqual(events, ['refresh'])

  finishRefresh()
  await resume

  assert.equal(controller.pending, false)
  assert.deepEqual(events, ['refresh', 'align', 'redraw', 'start'])
})

test('createPlaybackResumeController_reusesRefreshAcrossSuspendAndResume', async () => {
  assert.equal(typeof redrawScheduler.createPlaybackResumeController, 'function')
  let finishRefresh
  let refreshCount = 0
  const events = []
  const controller = redrawScheduler.createPlaybackResumeController({
    refreshStatus() {
      refreshCount += 1
      return new Promise(resolve => { finishRefresh = resolve })
    },
    shouldResume: () => true,
    align() { events.push('align') },
    redraw() { events.push('redraw') },
    start() { events.push('start') },
  })

  const firstResume = controller.resume()
  controller.suspend()
  const secondResume = controller.resume()

  assert.equal(refreshCount, 1)
  assert.equal(controller.pending, true)

  finishRefresh()
  await Promise.all([firstResume, secondResume])

  assert.deepEqual(events, ['align', 'redraw', 'start'])
})

test('musicStudio_playbackLoopRebasesElapsedTimeAfterVisibilityPause', () => {
  const deltas = []
  const frames = new Map()
  let nextFrameId = 1
  const loop = createPlaybackRedrawLoop({
    shouldTick: () => true,
    onTick(delta) {
      deltas.push(delta)
    },
    requestFrame(callback) {
      const frameId = nextFrameId
      nextFrameId += 1
      frames.set(frameId, callback)
      return frameId
    },
    cancelFrame(frameId) {
      frames.delete(frameId)
    },
  })

  function flushFrame(now) {
    const nextFrame = frames.entries().next().value
    assert.ok(nextFrame)
    const [frameId, callback] = nextFrame
    frames.delete(frameId)
    callback(now)
  }

  loop.start()
  flushFrame(1000)
  flushFrame(1500)
  loop.stop()
  loop.start()
  flushFrame(3500)

  assert.deepEqual(deltas, [0, 0.5, 0])
})
