<template>
  <div :class="['message', message.role]">
    <div
      v-if="message.role !== 'user'"
      class="msg-head"
    >
      <span class="msg-role">{{ message.role === 'user' ? 'You' : 'ATRI' }}</span>
      <span class="msg-time">{{ timeStr }}</span>
    </div>
    <div class="msg-body">
      <template v-if="message.role === 'user'">
        <div class="user-bubble">
          <div class="user-content">
            <pre
              v-if="message.content"
              class="msg-text user-text"
            >{{ message.content }}</pre>
            <div
              v-if="userAttachments.length"
              class="user-attachments"
            >
              <figure
                v-for="image in userAttachments"
                :key="image.id || image.src"
                class="user-image"
              >
                <img
                  :src="safeImageSrc(image.src)"
                  :alt="image.name || 'Attached image'"
                >
                <figcaption>{{ image.name || 'image' }}</figcaption>
              </figure>
            </div>
          </div>
          <span
            class="user-action"
            aria-hidden="true"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M9 14 4 9l5-5" />
              <path d="M4 9h11a5 5 0 0 1 0 10h-1" />
            </svg>
          </span>
        </div>
      </template>
      <template v-else-if="message.role === 'assistant' && message.md">
        <div
          class="markdown-body"
          @click="handleMarkdownClick"
          v-html="renderedContent"
        />
        <span
          v-if="message.streaming"
          class="stream-cursor"
        />
      </template>
      <template v-else>
        <pre class="msg-text">{{ message.content }}</pre>
        <span
          v-if="message.streaming"
          class="stream-cursor"
        />
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'

const props = defineProps({
  message: { type: Object, required: true },
})

const userAttachments = computed(() => (
  Array.isArray(props.message.attachments)
    ? props.message.attachments.filter((image) => safeImageSrc(image.src))
    : []
))

const timeStr = computed(() => {
  const d = props.message.time ? new Date(props.message.time) : new Date()
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
})

// Configure marked once
marked.setOptions({
  breaks: true,
  gfm: true,
})

const renderer = new marked.Renderer()
renderer.html = function (tokenOrHtml) {
  const raw = typeof tokenOrHtml === 'object'
    ? tokenOrHtml.raw || tokenOrHtml.text || ''
    : String(tokenOrHtml ?? '')
  return esc(raw)
}

renderer.link = function (tokenOrHref, title, text) {
  const href = typeof tokenOrHref === 'object' ? tokenOrHref.href || '' : String(tokenOrHref ?? '')
  const label = typeof tokenOrHref === 'object'
    ? this.parser.parseInline(tokenOrHref.tokens || [])
    : String(text ?? '')
  if (!isSafeUrl(href)) return label
  const rawTitle = typeof tokenOrHref === 'object' ? tokenOrHref.title : title
  const titleAttr = rawTitle ? ` title="${escAttr(rawTitle)}"` : ''
  return `<a href="${escAttr(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${label}</a>`
}

renderer.image = function (tokenOrHref, title, text) {
  const href = typeof tokenOrHref === 'object' ? tokenOrHref.href || '' : String(tokenOrHref ?? '')
  if (!isSafeUrl(href)) return ''
  const rawTitle = typeof tokenOrHref === 'object' ? tokenOrHref.title : title
  const alt = typeof tokenOrHref === 'object' ? tokenOrHref.text || '' : String(text ?? '')
  const titleAttr = rawTitle ? ` title="${escAttr(rawTitle)}"` : ''
  return `<img src="${escAttr(href)}" alt="${escAttr(alt)}"${titleAttr}>`
}

renderer.code = function (tokenOrCode, lang) {
  const code = typeof tokenOrCode === 'object' ? tokenOrCode.text || '' : String(tokenOrCode ?? '')
  const language = normalizeLanguage((typeof tokenOrCode === 'object' ? tokenOrCode.lang : lang) || 'text')
  const classLanguage = language.replace(/[^a-z0-9_-]/g, '-')
  let highlighted
  if (language !== 'text' && hljs.getLanguage(language)) {
    highlighted = hljs.highlight(code, { language }).value
  } else {
    highlighted = hljs.highlightAuto(code).value
  }
  return `<div class="code-header"><span>${esc(language)}</span><button class="btn-copy" type="button">Copy</button></div><pre><code class="hljs language-${escAttr(classLanguage)}">${highlighted}</code></pre>`
}

marked.use({ renderer })

const renderedContent = computed(() => {
  try {
    return marked.parse(props.message.content || '')
  } catch {
    return `<pre class="msg-text">${esc(props.message.content || '')}</pre>`
  }
})

function esc(s) {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}

function escAttr(s) {
  return esc(String(s ?? '')).replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function isSafeUrl(url) {
  const value = String(url || '').trim()
  if (!value) return false
  if (value.startsWith('#') || value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) {
    return true
  }
  try {
    const parsed = new URL(value, window.location.origin)
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol)
  } catch {
    return false
  }
}

function safeImageSrc(url) {
  const value = String(url || '').trim()
  if (!value) return ''
  if (/^data:image\/(?:png|jpe?g|webp|gif);base64,[a-z0-9+/=\s]+$/i.test(value)) {
    return value
  }
  try {
    const parsed = new URL(value, window.location.origin)
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : ''
  } catch {
    return ''
  }
}

function normalizeLanguage(value) {
  const firstToken = String(value || 'text').trim().split(/\s+/)[0].toLowerCase()
  if (!firstToken || firstToken.length > 40) return 'text'
  return /^[a-z0-9_+.-]+$/.test(firstToken) ? firstToken : 'text'
}

async function handleMarkdownClick(event) {
  const button = event.target?.closest?.('.btn-copy')
  if (!button) return
  const header = button.closest('.code-header')
  const code = header?.nextElementSibling?.querySelector?.('code')?.textContent || ''
  try {
    await navigator.clipboard.writeText(code)
    const oldText = button.textContent
    button.textContent = 'Copied'
    window.setTimeout(() => {
      button.textContent = oldText || 'Copy'
    }, 1200)
  } catch {
    button.textContent = 'Failed'
    window.setTimeout(() => {
      button.textContent = 'Copy'
    }, 1200)
  }
}
</script>

<style scoped>
.message {
  margin-bottom: 14px;
  max-width: 900px;
  margin-left: auto;
  margin-right: auto;
}

.message.user {
  max-width: 920px;
  margin-bottom: 14px;
}

.message.user .msg-body {
  display: flex;
  justify-content: flex-end;
}

.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 18px;
  margin-bottom: 2px;
  opacity: 0;
  transition: opacity 0.14s ease;
}

.message:hover .msg-head,
.message:focus-within .msg-head {
  opacity: 1;
}

.msg-role {
  font-weight: 600;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--t1);
}

.msg-time {
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.msg-body {
  font-size: 14px;
  line-height: 1.68;
  word-break: break-word;
}

.msg-text {
  white-space: pre-wrap;
  font-family: var(--sans);
  font-size: 14px;
}

.user-bubble {
  display: inline-flex;
  align-items: flex-start;
  gap: 12px;
  max-width: 100%;
  min-height: 42px;
  padding: 8px 12px;
  color: #f0f0f0;
  background: rgba(37, 37, 38, 0.86);
  border: 1px solid var(--border-input);
  border-radius: 8px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.user-content {
  min-width: 0;
  display: grid;
  gap: 8px;
}

.user-text {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  font-size: 14px;
  line-height: 1.45;
}

.user-attachments {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px;
  max-width: min(560px, 72vw);
}

.user-image {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 8px;
  background: rgba(15, 15, 15, 0.5);
}

.user-image img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
  background: rgba(255, 255, 255, 0.04);
}

.user-image figcaption {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 4px 6px 5px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
}

.user-action {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  color: var(--t3);
  opacity: 0;
  transition: opacity 0.14s ease, color 0.14s ease;
}

.message.user:hover .user-action,
.message.user:focus-within .user-action {
  opacity: 1;
}

.user-action svg {
  width: 16px;
  height: 16px;
}

.stream-cursor {
  display: inline-block;
  width: 7px;
  height: 1.2em;
  margin-left: 2px;
  vertical-align: -0.2em;
  background: var(--t2);
  animation: cursor-blink 1s steps(2, start) infinite;
}

@keyframes cursor-blink {
  to { visibility: hidden; }
}

/* Markdown rendered styles */
.markdown-body {
  color: var(--t1);
  max-width: 100%;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 14px 0 6px;
  color: var(--t1);
}

.markdown-body :deep(h1) {
  font-size: 1.3em;
  border-bottom: 1px solid var(--border);
  padding-bottom: 4px;
}

.markdown-body :deep(h2) { font-size: 1.15em; }
.markdown-body :deep(p) { margin: 6px 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0;
  padding-left: 22px;
}

.markdown-body :deep(a) { color: var(--acc2); }

.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--acc);
  padding: 4px 12px;
  margin: 8px 0;
  color: var(--t2);
  background: rgba(55, 148, 255, 0.04);
  border-radius: 0 4px 4px 0;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 5px 8px;
  font-size: 13px;
}

.markdown-body :deep(th) { background: var(--bg2); }

.markdown-body :deep(code:not(pre code)) {
  background: var(--bg2);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  font-family: var(--mono);
  color: var(--acc2);
}

.markdown-body :deep(pre) {
  margin: 8px 0;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--border);
}

.markdown-body :deep(pre code) {
  display: block;
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.5;
  font-family: var(--mono);
  color: var(--code-text);
  overflow-x: auto;
}

.markdown-body :deep(pre code.hljs) {
  background: var(--bg1);
}

.markdown-body :deep(.code-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 12px;
  background: var(--bg2);
  font-size: 11px;
  font-family: var(--mono);
  color: var(--t3);
  border-bottom: 1px solid var(--border);
}

.markdown-body :deep(.btn-copy) {
  background: none;
  border: 1px solid var(--border);
  color: var(--t3);
  padding: 1px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 10px;
  font-family: var(--mono);
}

.markdown-body :deep(.btn-copy:hover) {
  color: var(--t1);
  background: var(--bg3);
}
</style>
