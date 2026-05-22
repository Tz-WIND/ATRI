export const DEFAULT_CONTROLLER_IDS = [
  'velocity',
  'cc:1',
  'pitch_bend',
  'after_touch',
]

export const DEFAULT_NOTE_VELOCITY = 80

export const CONTROLLER_PRESETS = [
  { id: 'velocity', label: '力度' },
  { id: 'cc:1', label: 'Modulation' },
  { id: 'cc:2', label: 'Breath CC2' },
  { id: 'cc:7', label: 'Volume CC7' },
  { id: 'cc:10', label: 'Pan CC10' },
  { id: 'cc:11', label: 'Expression CC11' },
  { id: 'cc:64', label: 'Sustain CC64' },
  { id: 'pitch_bend', label: 'Pitch Bend' },
  { id: 'after_touch', label: 'After Touch' },
]

const PRESET_LABELS = new Map(CONTROLLER_PRESETS.map(item => [item.id, item.label]))

export function createDefaultControllerLanes() {
  return [
    {
      id: makeControllerLaneId(),
      activeControllerId: 'velocity',
      controllerIds: [...DEFAULT_CONTROLLER_IDS],
    },
  ]
}

export function makeControllerLaneId() {
  return `lane_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

export function controllerDefinitionFromId(id) {
  const controllerId = String(id || 'velocity')
  if (controllerId === 'velocity') {
    return {
      id: 'velocity',
      type: 'velocity',
      label: PRESET_LABELS.get('velocity') || '力度',
      min: 0,
      max: 127,
      defaultValue: DEFAULT_NOTE_VELOCITY,
    }
  }

  if (controllerId === 'pitch_bend') {
    return {
      id: 'pitch_bend',
      type: 'pitch_bend',
      label: PRESET_LABELS.get('pitch_bend') || 'Pitch Bend',
      min: -8192,
      max: 8191,
      defaultValue: 0,
    }
  }

  if (controllerId === 'after_touch') {
    return {
      id: 'after_touch',
      type: 'channel_pressure',
      label: PRESET_LABELS.get('after_touch') || 'After Touch',
      min: 0,
      max: 127,
      defaultValue: 0,
    }
  }

  const match = /^cc:(\d{1,3})$/.exec(controllerId)
  if (match) {
    const controller = clamp(Number(match[1]), 0, 127)
    const normalizedId = `cc:${controller}`
    return {
      id: normalizedId,
      type: 'control_change',
      label: PRESET_LABELS.get(normalizedId) || `CC${controller}`,
      min: 0,
      max: 127,
      defaultValue: 0,
      controller,
    }
  }

  return controllerDefinitionFromId('velocity')
}

export function eventMatchesController(event, definition) {
  if (!event || !definition || definition.type === 'velocity') return false
  if (definition.type === 'control_change') {
    return event.type === 'control_change'
      && Number(event.controller) === Number(definition.controller)
  }
  if (definition.type === 'pitch_bend') {
    return event.type === 'pitch_bend'
  }
  if (definition.type === 'channel_pressure') {
    return event.type === 'channel_pressure'
  }
  return false
}

export function normalizeControllerEvent(definition, event, snapStep = 0.25) {
  const controller = controllerDefinitionFromId(definition?.id)
  const value = clamp(
    Math.round(Number(event?.value ?? event?.pressure ?? controller.defaultValue)),
    controller.min,
    controller.max
  )
  const normalized = {
    id: String(event?.id || makeControllerEventId()),
    type: controller.type,
    start: Math.max(0, snapBeat(Number(event?.start || 0), snapStep)),
    channel: clamp(Math.round(Number(event?.channel ?? 0)), 0, 15),
  }

  if (controller.type === 'control_change') {
    return {
      ...normalized,
      controller: controller.controller,
      value,
    }
  }

  if (controller.type === 'pitch_bend') {
    return {
      ...normalized,
      value,
    }
  }

  if (controller.type === 'channel_pressure') {
    return {
      ...normalized,
      pressure: value,
    }
  }

  return normalized
}

export function makeControllerEventId() {
  return `evt_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

export function valueFromControllerEvent(event, definition) {
  if (!event || !definition) return 0
  if (definition.type === 'channel_pressure') {
    return Number(event.pressure ?? event.value ?? definition.defaultValue)
  }
  return Number(event.value ?? definition.defaultValue)
}

export function controllerRenderPoints(events, definition, tailBeat) {
  const controller = controllerDefinitionFromId(definition?.id)
  const actualPoints = (events || [])
    .filter(event => eventMatchesController(event, controller))
    .map(event => ({
      event,
      start: Math.max(0, Number(event.start || 0)),
      value: valueFromControllerEvent(event, controller),
      synthetic: false,
    }))
    .sort((a, b) => a.start - b.start)

  if (!actualPoints.length) return []

  const first = actualPoints[0]
  const last = actualPoints[actualPoints.length - 1]
  const end = Math.max(Number(tailBeat || 0), last.start)

  return [
    { start: 0, value: first.value, synthetic: true },
    ...actualPoints,
    { start: end, value: last.value, synthetic: true },
  ]
}

export function controllerDisplayRange(definition) {
  if (definition?.type === 'velocity') {
    return {
      min: 0,
      middle: 50,
      max: 100,
    }
  }
  const min = Number(definition?.min ?? 0)
  const max = Number(definition?.max ?? 127)
  return {
    min,
    middle: Math.round((min + max) / 2),
    max,
  }
}

export function controllerDisplayValue(definition, value) {
  if (definition?.type === 'velocity') {
    return clamp(Math.round(Number(value ?? DEFAULT_NOTE_VELOCITY)), 0, 100)
  }
  return Math.round(Number(value ?? definition?.defaultValue ?? 0))
}

export function controllerLaneStackHeight(laneCount, laneHeight = 96, footerHeight = 28) {
  const safeLaneCount = Math.max(1, Math.trunc(Number(laneCount || 0)))
  return safeLaneCount * Number(laneHeight || 0) + Number(footerHeight || 0)
}

export function controllerLaneColorStyles(trackColor) {
  return {
    velocityStroke: colorToRgba(trackColor, 0.92),
    velocityFill: colorToRgba(trackColor, 1),
    selectedVelocityStroke: '#f0d17a',
    selectedVelocityFill: '#ffe6a3',
    eventStroke: colorToRgba(trackColor, 0.78),
    eventFill: colorToRgba(trackColor, 0.92),
    eventPointStroke: 'rgba(0,0,0,0.38)',
  }
}

export function controllerValueToUnit(definition, value) {
  if (definition?.type === 'velocity') {
    return clamp(Number(value ?? DEFAULT_NOTE_VELOCITY), 0, 100) / 100
  }
  const min = Number(definition?.min ?? 0)
  const max = Number(definition?.max ?? 127)
  const range = Math.max(1, max - min)
  return (clamp(Number(value ?? definition?.defaultValue ?? min), min, max) - min) / range
}

export function controllerUnitToValue(definition, unit) {
  if (definition?.type === 'velocity') {
    return Math.round(clamp(Number(unit || 0), 0, 1) * 100)
  }
  const min = Number(definition?.min ?? 0)
  const max = Number(definition?.max ?? 127)
  const safeUnit = clamp(Number(unit || 0), 0, 1)
  return Math.round(min + safeUnit * (max - min))
}

function snapBeat(value, step) {
  const beat = Number(value || 0)
  if (!Number.isFinite(beat)) return 0
  if (!Number.isFinite(step) || Number(step) <= 0) {
    return Math.round(beat * 1000000) / 1000000
  }
  const safeStep = Number(step)
  return Math.round((Math.round(beat / safeStep) * safeStep) * 1000000) / 1000000
}

function colorToRgba(hex, alpha) {
  const safe = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#4e79ff'
  const value = parseInt(safe.slice(1), 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}
