<template>
  <div :class="['message', message.role]">
    <div
      v-if="message.role === 'assistant'"
      class="msg-head"
    >
      <span class="msg-role">{{ roleLabel }}</span>
      <span class="msg-time">{{ timeStr }}</span>
      <button
        v-if="assistantCopyAvailable"
        :class="['assistant-copy-button', assistantCopyState]"
        type="button"
        :aria-label="assistantCopyLabel"
        :title="assistantCopyLabel"
        @click="copyAssistantMessage"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          aria-hidden="true"
        >
          <rect
            x="9"
            y="9"
            width="13"
            height="13"
            rx="2"
          />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
        <span class="assistant-copy-status">{{ assistantCopyStatusText }}</span>
      </button>
    </div>
    <div class="msg-body">
      <template v-if="message.role === 'error'">
        <div
          :class="['error-card', `error-${message.errorKind || 'error'}`]"
          role="alert"
        >
          <div class="error-card-head">
            <svg
              class="error-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              aria-hidden="true"
            >
              <circle
                cx="12"
                cy="12"
                r="10"
              />
              <line
                x1="12"
                y1="8"
                x2="12"
                y2="12"
              />
              <circle
                cx="12"
                cy="16"
                r="0.6"
                fill="currentColor"
                stroke="none"
              />
            </svg>
            <span class="error-title">{{ message.title || 'Something went wrong' }}</span>
          </div>
          <p
            v-if="message.detail"
            class="error-detail"
          >
            {{ message.detail }}
          </p>
          <div
            v-if="message.retriable"
            class="error-actions"
          >
            <button
              type="button"
              class="error-retry-button"
              :disabled="retryDisabled"
              @click="emitRetry"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                aria-hidden="true"
              >
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
              <span>{{ retryDisabled ? 'Retrying…' : 'Retry' }}</span>
            </button>
          </div>
        </div>
      </template>
      <template v-else-if="message.role === 'user'">
        <div class="user-bubble">
          <div class="user-content">
            <pre
              v-if="message.content"
              class="msg-text user-text"
            >{{ message.content }}</pre>
            <div
              v-if="userAttachments.length || userFileAttachments.length"
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
              <div
                v-for="file in userFileAttachments"
                :key="file.id || file.name"
                class="user-file"
                :title="file.name"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                <span>{{ file.name || 'file' }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>
      <template v-else-if="message.role === 'assistant' && message.md">
        <pre
          v-if="streamingPlainText"
          class="msg-text assistant-stream-text"
        >{{ message.content }}<span
          v-if="message.streaming"
          class="stream-cursor"
        /></pre>
        <div
          v-else
          class="markdown-body"
          @click="handleMarkdownClick"
          v-html="renderedContentWithCursor"
        />
      </template>
      <template v-else>
        <pre class="msg-text">{{ message.content }}<span
          v-if="message.streaming"
          class="stream-cursor"
        /></pre>
      </template>
      <div
        v-if="message.role !== 'user' && message.role !== 'error' && assistantAttachments.length"
        class="assistant-attachments"
      >
        <figure
          v-for="image in assistantAttachments"
          :key="image.id || image.src"
          class="assistant-image"
        >
          <img
            :src="safeImageSrc(image.src)"
            :alt="image.name || 'Generated image'"
          >
        </figure>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import {
  escapeHtml,
  getAssistantMessageCopyText,
  renderChatMarkdown,
  shouldRenderStreamingPlainText,
} from './chatMarkdown.js'

const props = defineProps({
  message: { type: Object, required: true },
  retryDisabled: { type: Boolean, default: false },
})

const emit = defineEmits(['retry'])

const retrying = ref(false)

const roleLabel = computed(() => {
  switch (props.message.role) {
    case 'user': return 'You'
    case 'error': return 'System'
    default: return 'ATRI'
  }
})

// Disable the retry button while a retry is in flight or while the global send
// guard is holding. We let the parent disable via prop when needed, but the
// local retrying flag also tracks the click -> emit round-trip.
const retryDisabled = computed(() => retrying.value || props.retryDisabled)

const userAttachments = computed(() => (
  Array.isArray(props.message.attachments)
    ? props.message.attachments.filter((image) => safeImageSrc(image.src))
    : []
))

const userFileAttachments = computed(() => (
  Array.isArray(props.message.attachments)
    ? props.message.attachments.filter((file) => file?.kind === 'file')
    : []
))

const assistantAttachments = computed(() => (
  Array.isArray(props.message.attachments)
    ? props.message.attachments.filter((image) => safeImageSrc(image.src))
    : []
))

const timeStr = computed(() => {
  const d = props.message.time ? new Date(props.message.time) : new Date()
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
})

const streamingPlainText = computed(() => shouldRenderStreamingPlainText(props.message))

const assistantCopyState = ref('idle')
const markdownCopyResetTimers = new Map()
let assistantCopyResetTimer = null
let retryResetTimer = null
const assistantCopyText = computed(() => getAssistantMessageCopyText(props.message))
const assistantCopyAvailable = computed(() => assistantCopyText.value.length > 0)
const assistantCopyLabel = computed(() => {
  if (assistantCopyState.value === 'copied') return 'Copied assistant reply'
  if (assistantCopyState.value === 'failed') return 'Copy failed'
  return 'Copy assistant reply'
})
const assistantCopyStatusText = computed(() => (
  assistantCopyState.value === 'copied'
    ? 'Copied'
    : assistantCopyState.value === 'failed'
      ? 'Failed'
      : ''
))

const renderedContent = computed(() => {
  if (streamingPlainText.value) return ''
  try {
    return renderChatMarkdown(props.message.content || '')
  } catch {
    return `<pre class="msg-text">${escapeHtml(props.message.content || '')}</pre>`
  }
})

const renderedContentWithCursor = computed(() => {
  if (!props.message.streaming) return renderedContent.value
  return appendStreamingCursor(renderedContent.value)
})

function safeImageSrc(url) {
  const value = String(url || '').trim()
  if (!value) return ''
  // Generated chemistry drawings arrive as data:image/svg+xml attachments.
  if (/^data:image\/svg\+xml;base64,[a-z0-9+/=\s]+$/i.test(value)) {
    return value
  }
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

function clearMarkdownCopyTimer(button) {
  const timer = markdownCopyResetTimers.get(button)
  if (timer === undefined) return
  window.clearTimeout(timer)
  markdownCopyResetTimers.delete(button)
}

function scheduleMarkdownCopyReset(button, text) {
  clearMarkdownCopyTimer(button)
  const timer = window.setTimeout(() => {
    button.textContent = text
    markdownCopyResetTimers.delete(button)
  }, 1200)
  markdownCopyResetTimers.set(button, timer)
}

function clearAssistantCopyTimer() {
  if (assistantCopyResetTimer === null) return
  window.clearTimeout(assistantCopyResetTimer)
  assistantCopyResetTimer = null
}

function scheduleAssistantCopyReset() {
  clearAssistantCopyTimer()
  assistantCopyResetTimer = window.setTimeout(() => {
    assistantCopyState.value = 'idle'
    assistantCopyResetTimer = null
  }, 1200)
}

function clearRetryTimer() {
  if (retryResetTimer === null) return
  window.clearTimeout(retryResetTimer)
  retryResetTimer = null
}

function clearCopyTimers() {
  for (const timer of markdownCopyResetTimers.values()) {
    window.clearTimeout(timer)
  }
  markdownCopyResetTimers.clear()
  clearAssistantCopyTimer()
}

function clearMessageTimers() {
  clearCopyTimers()
  clearRetryTimer()
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
    scheduleMarkdownCopyReset(button, oldText || 'Copy')
  } catch {
    button.textContent = 'Failed'
    scheduleMarkdownCopyReset(button, 'Copy')
  }
}

function appendStreamingCursor(html) {
  const cursor = '<span class="stream-cursor" aria-hidden="true"></span>'
  const source = String(html || '')
  if (!source) return cursor
  const inlineEnd = /(<\/(?:p|li|h[1-6]|td|th)>\s*)$/i
  if (inlineEnd.test(source)) {
    return source.replace(inlineEnd, `${cursor}$1`)
  }
  return `${source}${cursor}`
}

async function copyAssistantMessage() {
  if (!assistantCopyAvailable.value) return
  try {
    await navigator.clipboard.writeText(assistantCopyText.value)
    assistantCopyState.value = 'copied'
  } catch {
    assistantCopyState.value = 'failed'
  }
  scheduleAssistantCopyReset()
}

async function emitRetry() {
  if (retryDisabled.value) return
  retrying.value = true
  try {
    emit('retry')
  } finally {
    // Re-enable shortly after emit; the parent drives the actual send and will
    // flip the error message out of the list on success or replace it on
    // failure. This is just a local guard against double-clicks.
    clearRetryTimer()
    retryResetTimer = window.setTimeout(() => {
      retrying.value = false
      retryResetTimer = null
    }, 800)
  }
}

onUnmounted(clearMessageTimers)
</script>

<style scoped>
.message {
  margin-bottom: 16px;
  max-width: 900px;
  margin-left: auto;
  margin-right: auto;
}

.message.assistant {
  margin-bottom: 18px;
}

.message.error {
  max-width: 900px;
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
  pointer-events: none;
  transition: opacity 0.14s ease;
}

.message:hover .msg-head,
.message:focus-within .msg-head {
  opacity: 1;
  pointer-events: auto;
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
  position: relative;
  font-size: 14px;
  line-height: 1.68;
  word-break: break-word;
}

.msg-text {
  white-space: pre-wrap;
  font-family: var(--sans);
  font-size: 14px;
}

.assistant-stream-text {
  margin: 0;
  color: var(--t1);
}

.user-bubble {
  display: inline-flex;
  align-items: flex-start;
  gap: 12px;
  max-width: 100%;
  min-height: 42px;
  padding: 9px 12px;
  color: var(--t1);
  background: rgba(255, 255, 255, 0.055);
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
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: rgba(24, 24, 24, 0.5);
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

.user-file {
  min-width: 0;
  height: 36px;
  display: flex;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: rgba(24, 24, 24, 0.5);
  color: var(--t2);
  padding: 0 9px;
}

.user-file svg {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--acc2);
}

.user-file span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--mono);
  font-size: 10px;
}

.stream-cursor,
.markdown-body :deep(.stream-cursor) {
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
  border-left: 2px solid var(--acc);
  padding: 4px 12px;
  margin: 8px 0;
  color: var(--t2);
  background: var(--acc-bg);
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
  border-radius: 8px;
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
  background: rgba(255, 255, 255, 0.045);
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
  border-radius: 6px;
  cursor: pointer;
  font-size: 10px;
  font-family: var(--mono);
}

.markdown-body :deep(.btn-copy:hover) {
  color: var(--t1);
  background: var(--bg-100);
}

.markdown-body :deep(.math) {
  font-family: "Cambria Math", "STIX Two Math", "Times New Roman", serif;
  color: var(--t1);
}

.markdown-body :deep(.math-display) {
  display: block;
  overflow-x: auto;
  max-width: 100%;
  margin: 10px 0;
  padding: 8px 0;
}

.markdown-body :deep(.math-inline) {
  display: inline-flex;
  align-items: baseline;
  max-width: 100%;
  vertical-align: -0.12em;
}

.markdown-body :deep(.math math) {
  max-width: 100%;
}

.assistant-attachments {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 360px));
  gap: 10px;
  margin-top: 10px;
}

.assistant-image {
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(24, 24, 24, 0.52);
}

.assistant-image img {
  width: 100%;
  display: block;
  max-height: 520px;
  object-fit: contain;
  background: rgba(255, 255, 255, 0.03);
}

.assistant-copy-button {
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: var(--t3);
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid var(--border-light);
  border-radius: 6px;
  cursor: pointer;
  transition:
    color 0.14s ease,
    background 0.14s ease,
    border-color 0.14s ease;
}

.assistant-copy-button:hover,
.assistant-copy-button:focus-visible {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-strong);
  outline: none;
}

.assistant-copy-button.copied {
  color: var(--ok);
  border-color: rgba(143, 216, 199, 0.42);
}

.assistant-copy-button.failed {
  color: var(--red);
  border-color: rgba(255, 116, 116, 0.42);
}

.assistant-copy-button svg {
  width: 14px;
  height: 14px;
}

.assistant-copy-status {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* Error message card */
.error-card {
  border: 1px solid rgba(255, 141, 127, 0.34);
  border-left-width: 3px;
  border-radius: 8px;
  background: var(--red-bg);
  padding: 11px 14px 12px;
  color: var(--t1);
  animation: error-in 0.18s ease-out;
}

@keyframes error-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.error-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.error-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--red);
}

.error-title {
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 650;
  color: var(--red);
  letter-spacing: 0.01em;
}

.error-detail {
  margin: 6px 0 0 23px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--t2);
  white-space: pre-wrap;
  word-break: break-word;
}

.error-actions {
  margin: 10px 0 0 23px;
}

.error-retry-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 12px;
  font-family: var(--mono);
  font-weight: 600;
  color: var(--t1);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.14s ease, border-color 0.14s ease, opacity 0.14s ease;
}

.error-retry-button:hover:not(:disabled) {
  background: var(--bg-200);
  border-color: var(--red);
}

.error-retry-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.error-retry-button svg {
  width: 13px;
  height: 13px;
}
</style>
