import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clamp,
  curveRenderSampleCount,
  hexToRgba,
  mixHexColor,
  roundRect,
  setupCanvas,
} from './canvasUtils.js'

test('setupCanvas_scalesBackingStoreByDevicePixelRatio', () => {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  globalThis.window = { devicePixelRatio: 2 }
  let requestedContext = null
  let transform = null
  const ctx = {
    setTransform(...args) {
      transform = args
    },
  }
  const canvas = {
    style: {},
    getContext(type) {
      requestedContext = type
      return ctx
    },
  }

  try {
    assert.equal(setupCanvas(canvas, 100.4, 50.6), ctx)
  } finally {
    if (hadWindow) {
      globalThis.window = previousWindow
    } else {
      delete globalThis.window
    }
  }

  assert.equal(canvas.width, 200)
  assert.equal(canvas.height, 101)
  assert.equal(canvas.style.width, '100.4px')
  assert.equal(canvas.style.height, '50.6px')
  assert.equal(requestedContext, '2d')
  assert.deepEqual(transform, [2, 0, 0, 2, 0, 0])
})

test('setupCanvas_keepsBackingStoreWhenSizeIsUnchanged', () => {
  const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
  const previousWindow = globalThis.window
  globalThis.window = { devicePixelRatio: 2 }
  let width = 200
  let height = 100
  let widthWrites = 0
  let heightWrites = 0
  let styleWidthWrites = 0
  let styleHeightWrites = 0
  const style = {}
  Object.defineProperties(style, {
    width: {
      get: () => '100px',
      set: () => { styleWidthWrites += 1 },
    },
    height: {
      get: () => '50px',
      set: () => { styleHeightWrites += 1 },
    },
  })
  const ctx = {
    setTransform() {},
  }
  const canvas = {
    style,
    get width() {
      return width
    },
    set width(value) {
      widthWrites += 1
      width = value
    },
    get height() {
      return height
    },
    set height(value) {
      heightWrites += 1
      height = value
    },
    getContext() {
      return ctx
    },
  }

  try {
    assert.equal(setupCanvas(canvas, 100, 50), ctx)
  } finally {
    if (hadWindow) {
      globalThis.window = previousWindow
    } else {
      delete globalThis.window
    }
  }

  assert.equal(widthWrites, 0)
  assert.equal(heightWrites, 0)
  assert.equal(styleWidthWrites, 0)
  assert.equal(styleHeightWrites, 0)
})

test('canvasColorUtils_useFallbackAndMixColors', () => {
  assert.equal(hexToRgba('#12abef', 0.5), 'rgba(18, 171, 239, 0.5)')
  assert.equal(hexToRgba('bad-color', 1), 'rgba(78, 121, 255, 1)')
  assert.equal(mixHexColor('#000000', '#ffffff', 0.5), '#808080')
})

test('curveRenderSampleCount_scalesWithWidthAndStaysBounded', () => {
  assert.equal(curveRenderSampleCount(0, 0.1, 10), 4)
  assert.equal(curveRenderSampleCount(0, 16, 80), 64)
  assert.equal(clamp(20, 0, 10), 10)
})

test('roundRect_buildsClampedRoundedPath', () => {
  const ops = []
  const ctx = {
    beginPath: () => ops.push(['beginPath']),
    moveTo: (...args) => ops.push(['moveTo', ...args]),
    arcTo: (...args) => ops.push(['arcTo', ...args]),
    closePath: () => ops.push(['closePath']),
  }

  roundRect(ctx, 10, 20, 12, 8, 20)

  assert.deepEqual(ops[0], ['beginPath'])
  assert.deepEqual(ops[1], ['moveTo', 14, 20])
  assert.deepEqual(ops.at(-1), ['closePath'])
})
