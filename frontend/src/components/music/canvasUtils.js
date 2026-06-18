export function setupCanvas(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.max(1, Math.floor(width * dpr))
  canvas.height = Math.max(1, Math.floor(height * dpr))
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return ctx
}

export function curveRenderSampleCount(startBeat, endBeat, pxPerBeat) {
  const width = Math.abs(Number(endBeat || 0) - Number(startBeat || 0)) * Math.max(1, pxPerBeat)
  return Math.round(clamp(Math.ceil(width / 10), 4, 64))
}

export function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + width, y, x + width, y + height, r)
  ctx.arcTo(x + width, y + height, x, y + height, r)
  ctx.arcTo(x, y + height, x, y, r)
  ctx.arcTo(x, y, x + width, y, r)
  ctx.closePath()
}

export function hexToRgba(hex, alpha) {
  const { r, g, b } = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function hexToRgb(hex) {
  const safe = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#4e79ff'
  const value = parseInt(safe.slice(1), 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return { r, g, b }
}

export function mixHexColor(hex, targetHex, amount) {
  const source = hexToRgb(hex)
  const target = hexToRgb(targetHex)
  const unit = clamp(amount, 0, 1)
  const mixed = {
    r: Math.round(source.r + (target.r - source.r) * unit),
    g: Math.round(source.g + (target.g - source.g) * unit),
    b: Math.round(source.b + (target.b - source.b) * unit),
  }
  return rgbToHex(mixed)
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

function rgbToHex({ r, g, b }) {
  return `#${[r, g, b].map(value => value.toString(16).padStart(2, '0')).join('')}`
}
