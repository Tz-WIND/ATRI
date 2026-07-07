export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function escapeHtmlAttribute(value) {
  return escapeHtml(value)
}

export function shouldRenderStreamingPlainText(message) {
  return Boolean(message?.role === 'assistant' && message?.md && message?.streaming)
}

export function getAssistantMessageCopyText(message) {
  if (message?.role !== 'assistant' || message?.streaming) return ''
  const text = typeof message?.content === 'string' ? message.content : ''
  return text.trim() ? text : ''
}

export function normalizeLanguage(value) {
  const firstToken = String(value || 'text').trim().split(/\s+/)[0].toLowerCase()
  if (!firstToken || firstToken.length > 40) return 'text'
  return /^[a-z0-9_+.-]+$/.test(firstToken) ? firstToken : 'text'
}

export function highlightCode(code, language, hljs) {
  if (language !== 'text' && hljs.getLanguage(language)) {
    return hljs.highlight(code, { language }).value
  }
  return escapeHtml(code)
}
