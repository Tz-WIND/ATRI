const DENOMINATORS = [2, 4, 8, 16, 32]

export function normalizeMeterEvents(project) {
  const baseMeter = Array.isArray(project?.time_signature) ? project.time_signature : [4, 4]
  const byBeat = new Map()
  const pushEvent = (event) => {
    const beat = roundBeat(Math.max(0, Number(event?.beat || 0)))
    byBeat.set(beat, {
      beat,
      numerator: normalizeNumerator(event?.numerator),
      denominator: normalizeDenominator(event?.denominator),
    })
  }

  pushEvent({ beat: 0, numerator: baseMeter[0], denominator: baseMeter[1] })
  for (const event of project?.meter_events || []) {
    pushEvent(event)
  }

  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

export function effectiveMeterAtBeat(project, beat) {
  const safeBeat = Math.max(0, Number(beat || 0))
  let meter = normalizeMeterEvents(project)[0] || { beat: 0, numerator: 4, denominator: 4 }
  for (const event of normalizeMeterEvents(project)) {
    if (event.beat > safeBeat) break
    meter = event
  }
  return { numerator: meter.numerator, denominator: meter.denominator }
}

export function meterBarLinesBetween(project, startBeat, endBeat) {
  const start = Math.max(0, Number(startBeat || 0))
  const end = Math.max(start, Number(endBeat || 0))
  const lines = []
  for (const segment of meterSegments(project, start, end)) {
    for (
      let beat = firstMultipleAtOrAfter(segment.start, segment.barLength, segment.anchor);
      beat <= segment.end + 0.0001;
      beat += segment.barLength
    ) {
      if (beat < start - 0.0001 || beat > end + 0.0001) continue
      lines.push({
        beat: roundBeat(beat),
        bar: segment.barAtAnchor + Math.round((beat - segment.anchor) / segment.barLength),
        numerator: segment.numerator,
        denominator: segment.denominator,
      })
    }
  }
  return dedupeBarLines(lines)
}

export function meterPositionAtBeat(project, beat) {
  const safeBeat = Math.max(0, Number(beat || 0))
  const matchingSegments = meterSegments(project, safeBeat, safeBeat)
  const segment = matchingSegments[matchingSegments.length - 1] || baseSegment(project)
  const barsSinceAnchor = Math.floor((safeBeat - segment.anchor + 0.000001) / segment.barLength)
  const bar = segment.barAtAnchor + barsSinceAnchor
  const barStart = segment.anchor + barsSinceAnchor * segment.barLength
  const posInBar = Math.max(0, safeBeat - barStart)
  const beatInBar = Math.floor((posInBar + 0.000001) / segment.beatUnit) + 1
  const ticks = Math.floor(((posInBar % segment.beatUnit) / segment.beatUnit) * 960)
  return {
    bar,
    beat: beatInBar,
    ticks,
    numerator: segment.numerator,
    denominator: segment.denominator,
  }
}

export function meterSegments(project, startBeat, endBeat) {
  const events = normalizeMeterEvents(project)
  const start = Math.max(0, Number(startBeat || 0))
  const end = Math.max(start, Number(endBeat || 0))
  const segments = []
  let barAtAnchor = 1

  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    const next = events[index + 1]
    const segmentEnd = next ? next.beat : end
    const segment = makeSegment(event, Math.max(event.beat, start), Math.min(segmentEnd, end), barAtAnchor)
    if (segment.end >= start && segment.start <= end) {
      segments.push(segment)
    }
    if (next) {
      barAtAnchor += Math.max(0, Math.ceil((next.beat - event.beat) / segment.barLength))
    }
  }

  return segments.length ? segments : [baseSegment(project)]
}

function baseSegment(project) {
  const meter = normalizeMeterEvents(project)[0] || { beat: 0, numerator: 4, denominator: 4 }
  return makeSegment(meter, 0, 0, 1)
}

function makeSegment(event, start, end, barAtAnchor) {
  const barLength = event.numerator * (4 / event.denominator)
  return {
    start,
    end,
    anchor: event.beat,
    barAtAnchor,
    numerator: event.numerator,
    denominator: event.denominator,
    barLength,
    beatUnit: barLength / event.numerator,
  }
}

function dedupeBarLines(lines) {
  const byBeat = new Map()
  for (const line of lines) {
    byBeat.set(roundBeat(line.beat), line)
  }
  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

function firstMultipleAtOrAfter(value, step, origin = 0) {
  if (!Number.isFinite(step) || step <= 0) return origin
  return origin + Math.ceil(((value - origin) - 0.000001) / step) * step
}

function normalizeNumerator(value) {
  const parsed = Math.round(Number(value))
  if (!Number.isFinite(parsed)) return 4
  return Math.max(1, Math.min(255, parsed))
}

function normalizeDenominator(value) {
  const parsed = Number.parseInt(value, 10)
  return DENOMINATORS.includes(parsed) ? parsed : 4
}

function roundBeat(value) {
  return Math.round(Number(value || 0) * 1000000) / 1000000
}
