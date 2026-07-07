import hljs from 'highlight.js'
import { Marked, Renderer } from 'marked'

import { renderMarkdownWithMath } from './mathRenderer.js'

const chatMarkdownRenderer = new Renderer()

chatMarkdownRenderer.html = function (tokenOrHtml) {
  const raw = typeof tokenOrHtml === 'object'
    ? tokenOrHtml.raw || tokenOrHtml.text || ''
    : String(tokenOrHtml ?? '')
  return escapeHtml(raw)
}

chatMarkdownRenderer.link = function (tokenOrHref, title, text) {
  const href = typeof tokenOrHref === 'object' ? tokenOrHref.href || '' : String(tokenOrHref ?? '')
  const label = typeof tokenOrHref === 'object'
    ? this.parser.parseInline(tokenOrHref.tokens || [])
    : String(text ?? '')
  if (!isSafeMarkdownUrl(href)) return label
  const rawTitle = typeof tokenOrHref === 'object' ? tokenOrHref.title : title
  const titleAttr = rawTitle ? ` title="${escapeHtmlAttribute(rawTitle)}"` : ''
  return `<a href="${escapeHtmlAttribute(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${label}</a>`
}

chatMarkdownRenderer.image = function (tokenOrHref, title, text) {
  const href = typeof tokenOrHref === 'object' ? tokenOrHref.href || '' : String(tokenOrHref ?? '')
  if (!isSafeMarkdownUrl(href)) return ''
  const rawTitle = typeof tokenOrHref === 'object' ? tokenOrHref.title : title
  const alt = typeof tokenOrHref === 'object' ? tokenOrHref.text || '' : String(text ?? '')
  const titleAttr = rawTitle ? ` title="${escapeHtmlAttribute(rawTitle)}"` : ''
  return `<img src="${escapeHtmlAttribute(href)}" alt="${escapeHtmlAttribute(alt)}"${titleAttr}>`
}

chatMarkdownRenderer.code = function (tokenOrCode, lang) {
  const code = typeof tokenOrCode === 'object' ? tokenOrCode.text || '' : String(tokenOrCode ?? '')
  const language = normalizeLanguage((typeof tokenOrCode === 'object' ? tokenOrCode.lang : lang) || 'text')
  const classLanguage = language.replace(/[^a-z0-9_-]/g, '-')
  const highlighted = highlightCode(code, language, hljs)
  return `<div class="code-header"><span>${escapeHtml(language)}</span><button class="btn-copy" type="button">Copy</button></div><pre><code class="hljs language-${escapeHtmlAttribute(classLanguage)}">${highlighted}</code></pre>`
}

const chatMarked = new Marked({
  breaks: true,
  gfm: true,
  renderer: chatMarkdownRenderer,
})

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

export function renderChatMarkdown(markdown) {
  return renderMarkdownWithMath(markdown, (source) => chatMarked.parse(source))
}

function isSafeMarkdownUrl(url) {
  const value = String(url || '').trim()
  if (!value) return false
  if (value.startsWith('#') || value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) {
    return true
  }
  try {
    const origin = globalThis.window?.location?.origin || globalThis.location?.origin || 'http://localhost'
    const parsed = new URL(value, origin)
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol)
  } catch {
    return false
  }
}
