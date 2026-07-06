import katex from 'katex'

const PLACEHOLDER_PREFIX = 'ATRI_MATH_PLACEHOLDER_'

const KATEX_OPTIONS = {
  output: 'htmlAndMathml',
  strict: false,
  throwOnError: false,
  trust: false,
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
  const className = segment.kind === 'display' ? 'math math-display' : 'math math-inline'
  const parsed = parseLatexToHtml(segment.latex, segment.kind)
  return `<span class="${className}" aria-label="${escapeAttribute(segment.latex)}">${parsed}</span>`
}

function parseLatexToHtml(latex, kind) {
  try {
    return katex.renderToString(String(latex || ''), {
      ...KATEX_OPTIONS,
      displayMode: kind === 'display',
    })
  } catch {
    return `<code class="math-error">${escapeHtml(latex)}</code>`
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
