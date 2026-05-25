const PLACEHOLDER_PREFIX = 'ATRI_MATH_PLACEHOLDER_'

const COMMANDS = {
  alpha: '<mi>&#x03B1;</mi>',
  beta: '<mi>&#x03B2;</mi>',
  gamma: '<mi>&#x03B3;</mi>',
  delta: '<mi>&#x03B4;</mi>',
  epsilon: '<mi>&#x03B5;</mi>',
  theta: '<mi>&#x03B8;</mi>',
  lambda: '<mi>&#x03BB;</mi>',
  mu: '<mi>&#x03BC;</mi>',
  pi: '<mi>&#x03C0;</mi>',
  sigma: '<mi>&#x03C3;</mi>',
  omega: '<mi>&#x03C9;</mi>',
  infty: '<mi>&#x221E;</mi>',
  int: '<mo>&#x222B;</mo>',
  sum: '<mo>&#x2211;</mo>',
  prod: '<mo>&#x220F;</mo>',
  neq: '<mo>&#x2260;</mo>',
  leq: '<mo>&#x2264;</mo>',
  geq: '<mo>&#x2265;</mo>',
  times: '<mo>&#x00D7;</mo>',
  cdot: '<mo>&#x22C5;</mo>',
  pm: '<mo>&#x00B1;</mo>',
  ln: '<mi mathvariant="normal">ln</mi>',
  sin: '<mi mathvariant="normal">sin</mi>',
  cos: '<mi mathvariant="normal">cos</mi>',
  tan: '<mi mathvariant="normal">tan</mi>',
  log: '<mi mathvariant="normal">log</mi>',
}

export function renderMarkdownWithMath(markdown, parseMarkdown) {
  const segments = []
  const markdownWithPlaceholders = extractMathPlaceholders(String(markdown || ''), segments)
  const html = String(parseMarkdown(markdownWithPlaceholders) || '')
  return restoreMathPlaceholders(html, segments)
}

function extractMathPlaceholders(source, segments) {
  let result = ''
  let i = 0
  let fenceMarker = ''

  while (i < source.length) {
    const fence = matchFenceAtLineStart(source, i)
    if (fence) {
      const lineEnd = findLineEnd(source, i)
      const line = source.slice(i, lineEnd)
      if (!fenceMarker) {
        fenceMarker = fence.marker
      } else if (fence.marker[0] === fenceMarker[0] && fence.marker.length >= fenceMarker.length) {
        fenceMarker = ''
      }
      result += line
      i = lineEnd
      continue
    }

    if (fenceMarker) {
      result += source[i]
      i += 1
      continue
    }

    const codeSpanEnd = findInlineCodeSpanEnd(source, i)
    if (codeSpanEnd !== -1) {
      result += source.slice(i, codeSpanEnd)
      i = codeSpanEnd
      continue
    }

    if (source.startsWith('\\[', i) && !isEscaped(source, i)) {
      const end = findClosingCommandDelimiter(source, i + 2, '\\]')
      if (end !== -1) {
        result += addMathSegment(segments, 'display', source.slice(i + 2, end))
        i = end + 2
        continue
      }
    }

    if (source.startsWith('\\(', i) && !isEscaped(source, i)) {
      const end = findClosingCommandDelimiter(source, i + 2, '\\)')
      if (end !== -1) {
        result += addMathSegment(segments, 'inline', source.slice(i + 2, end))
        i = end + 2
        continue
      }
    }

    if (source.startsWith('$$', i) && !isEscaped(source, i)) {
      const end = findClosingDisplayDelimiter(source, i + 2)
      if (end !== -1) {
        result += addMathSegment(segments, 'display', source.slice(i + 2, end))
        i = end + 2
        continue
      }
    }

    if (source[i] === '$' && source[i + 1] !== '$' && !isEscaped(source, i) && !/\s/.test(source[i + 1] || '')) {
      const end = findClosingInlineDelimiter(source, i + 1)
      if (end !== -1 && !/\s/.test(source[end - 1] || '')) {
        result += addMathSegment(segments, 'inline', source.slice(i + 1, end))
        i = end + 1
        continue
      }
    }

    result += source[i]
    i += 1
  }

  return result
}

function restoreMathPlaceholders(html, segments) {
  return segments.reduce((currentHtml, segment, index) => {
    const placeholder = `${PLACEHOLDER_PREFIX}${index}`
    return currentHtml.split(placeholder).join(renderMathSegment(segment))
  }, html)
}

function addMathSegment(segments, kind, latex) {
  const index = segments.length
  segments.push({ kind, latex: latex.trim() })
  return `${PLACEHOLDER_PREFIX}${index}`
}

function renderMathSegment(segment) {
  const displayAttr = segment.kind === 'display' ? ' display="block"' : ''
  const className = segment.kind === 'display' ? 'math math-display' : 'math math-inline'
  const parsed = parseLatexToMathml(segment.latex)
  return `<span class="${className}" aria-label="${escapeAttribute(segment.latex)}"><math${displayAttr}>${parsed}</math></span>`
}

function parseLatexToMathml(latex) {
  const parser = new LatexParser(latex)
  return parser.parseExpression()
}

class LatexParser {
  constructor(source) {
    this.source = String(source || '')
    this.index = 0
  }

  parseExpression(stopChar = '') {
    const nodes = []
    while (this.index < this.source.length) {
      const current = this.peek()
      if (stopChar && current === stopChar) break
      if (/\s/.test(current)) {
        this.index += 1
        continue
      }

      const atom = this.parseAtom()
      if (atom) nodes.push(this.parseScripts(atom))
    }

    if (stopChar && this.peek() === stopChar) this.index += 1
    return nodes.length === 1 ? nodes[0] : `<mrow>${nodes.join('')}</mrow>`
  }

  parseAtom() {
    const current = this.peek()
    if (!current) return ''
    if (current === '{') {
      this.index += 1
      return this.parseExpression('}')
    }
    if (current === '\\') return this.parseCommand()
    if (/[0-9.]/.test(current)) return this.parseNumber()
    if (/[A-Za-z]/.test(current)) {
      this.index += 1
      return `<mi>${escapeHtml(current)}</mi>`
    }

    this.index += 1
    return this.parseSymbol(current)
  }

  parseCommand() {
    this.index += 1
    const name = this.readCommandName()
    if (!name) return ''
    if (name === 'frac') {
      const numerator = this.parseRequiredGroup()
      const denominator = this.parseRequiredGroup()
      return `<mfrac>${numerator}${denominator}</mfrac>`
    }
    if (name === 'hat') {
      const value = this.parseRequiredGroup()
      return `<mover>${value}<mo>^</mo></mover>`
    }
    if (name === 'sqrt') {
      return `<msqrt>${this.parseRequiredGroup()}</msqrt>`
    }
    if (name === 'text') {
      return `<mtext>${escapeHtml(this.readRequiredTextGroup())}</mtext>`
    }
    if (name === 'xrightarrow') {
      const label = this.parseRequiredGroup()
      return `<mover><mo stretchy="true">&#x2192;</mo>${label}</mover>`
    }
    if (name === 'quad') return '<mspace width="1em"></mspace>'
    if (name === ',') return '<mspace width="0.167em"></mspace>'

    return COMMANDS[name] || `<mi>${escapeHtml(name)}</mi>`
  }

  parseRequiredGroup() {
    this.skipSpaces()
    if (this.peek() !== '{') return this.parseAtom()
    this.index += 1
    return this.parseExpression('}')
  }

  readRequiredTextGroup() {
    this.skipSpaces()
    if (this.peek() !== '{') {
      const start = this.index
      while (this.peek() && !/\s/.test(this.peek())) this.index += 1
      return this.source.slice(start, this.index)
    }

    this.index += 1
    let text = ''
    let depth = 1
    while (this.index < this.source.length && depth > 0) {
      const current = this.peek()
      const next = this.source[this.index + 1] || ''
      if (current === '\\' && (next === '{' || next === '}')) {
        text += next
        this.index += 2
        continue
      }
      if (current === '{') {
        depth += 1
        text += current
        this.index += 1
        continue
      }
      if (current === '}') {
        depth -= 1
        if (depth > 0) text += current
        this.index += 1
        continue
      }
      text += current
      this.index += 1
    }
    return text
  }

  parseScripts(base) {
    let subscript = ''
    let superscript = ''

    while (this.peek() === '_' || this.peek() === '^') {
      const marker = this.peek()
      this.index += 1
      const value = this.parseScriptValue()
      if (marker === '_') {
        subscript = value
      } else {
        superscript = value
      }
    }

    if (subscript && superscript) return `<msubsup>${base}${subscript}${superscript}</msubsup>`
    if (subscript) return `<msub>${base}${subscript}</msub>`
    if (superscript) return `<msup>${base}${superscript}</msup>`
    return base
  }

  parseScriptValue() {
    this.skipSpaces()
    if (this.peek() === '{') {
      this.index += 1
      return this.parseExpression('}')
    }
    return this.parseAtom()
  }

  parseNumber() {
    const start = this.index
    while (/[0-9.]/.test(this.peek())) this.index += 1
    return `<mn>${escapeHtml(this.source.slice(start, this.index))}</mn>`
  }

  parseSymbol(symbol) {
    if ('=+-*/(),[]|'.includes(symbol)) return `<mo>${escapeHtml(symbol)}</mo>`
    if (symbol === '<') return '<mo>&lt;</mo>'
    if (symbol === '>') return '<mo>&gt;</mo>'
    return `<mi>${escapeHtml(symbol)}</mi>`
  }

  readCommandName() {
    const start = this.index
    while (/[A-Za-z]/.test(this.peek())) this.index += 1
    if (this.index > start) return this.source.slice(start, this.index)

    const single = this.peek()
    this.index += 1
    return single
  }

  skipSpaces() {
    while (/\s/.test(this.peek())) this.index += 1
  }

  peek() {
    return this.source[this.index] || ''
  }
}

function matchFenceAtLineStart(source, index) {
  if (index !== 0 && source[index - 1] !== '\n') return null
  const rest = source.slice(index)
  const match = rest.match(/^[ \t]*(`{3,}|~{3,})/)
  return match ? { marker: match[1] } : null
}

function findLineEnd(source, index) {
  const nextLine = source.indexOf('\n', index)
  return nextLine === -1 ? source.length : nextLine + 1
}

function findClosingDisplayDelimiter(source, index) {
  for (let i = index; i < source.length - 1; i += 1) {
    if (source.startsWith('$$', i) && !isEscaped(source, i)) return i
  }
  return -1
}

function findClosingInlineDelimiter(source, index) {
  for (let i = index; i < source.length; i += 1) {
    if (source[i] === '\n') return -1
    if (source[i] === '$' && source[i + 1] !== '$' && !isEscaped(source, i)) return i
  }
  return -1
}

function findClosingCommandDelimiter(source, index, delimiter) {
  for (let i = index; i < source.length - 1; i += 1) {
    if (source.startsWith(delimiter, i) && !isEscaped(source, i)) return i
  }
  return -1
}

function findInlineCodeSpanEnd(source, index) {
  if (source[index] !== '`' || isEscaped(source, index)) return -1

  let markerLength = 0
  while (source[index + markerLength] === '`') markerLength += 1
  const marker = '`'.repeat(markerLength)
  const end = source.indexOf(marker, index + markerLength)
  return end === -1 ? -1 : end + markerLength
}

function isEscaped(source, index) {
  let slashCount = 0
  for (let i = index - 1; i >= 0 && source[i] === '\\'; i -= 1) {
    slashCount += 1
  }
  return slashCount % 2 === 1
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, '&#96;')
}
