<template>
  <div class="input-area">
    <transition name="composer-pop">
      <div
        v-if="activePanel"
        ref="popover"
        class="composer-popover"
        :class="{ 'is-modal-panel': hasPanelSearch }"
      >
        <div class="panel-head">
          <div>
            <div class="panel-title">
              {{ panelTitle }}
            </div>
            <div class="panel-subtitle">
              {{ panelSubtitle }}
            </div>
          </div>
          <div class="panel-actions">
            <div
              v-if="panelMeta"
              class="panel-meta"
            >
              {{ panelMeta }}
            </div>
            <button
              class="panel-close"
              type="button"
              title="Close"
              @pointerdown.prevent.stop="closePanel"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="M18 6 6 18" /><path d="m6 6 12 12" />
              </svg>
            </button>
          </div>
        </div>

        <input
          v-if="hasPanelSearch"
          ref="panelSearch"
          v-model="panelQuery"
          class="panel-search"
          type="text"
          spellcheck="false"
          :placeholder="panelSearchPlaceholder"
          @input="selectedIndex = 0"
          @keydown.stop="onPanelSearchKeydown"
        >

        <div
          v-if="panelItems.length === 0"
          class="panel-empty"
        >
          {{ panelEmptyText }}
        </div>
        <div
          v-else
          class="panel-list"
        >
          <button
            v-for="(item, index) in panelItems"
            :key="item.id"
            :class="['panel-item', { selected: index === selectedIndex }]"
            type="button"
            @mouseenter="selectedIndex = index"
            @mousedown.prevent="applyPanelItem(index)"
          >
            <span class="item-main">
              <span class="item-label">{{ item.label }}</span>
              <span class="item-desc">{{ item.description }}</span>
            </span>
            <span
              v-if="item.meta"
              class="item-meta"
            >{{ item.meta }}</span>
          </button>
        </div>

        <div class="panel-footer">
          <span>Enter apply</span>
          <span>Esc close</span>
          <span>Up/Down move</span>
        </div>
      </div>
    </transition>

    <div
      v-if="statusMessage"
      class="composer-status"
    >
      {{ statusMessage }}
    </div>

    <div
      v-if="queuedDrafts.length"
      class="draft-queue"
    >
      <span class="queue-count">{{ queuedDrafts.length }}</span>
      <span class="queue-label">Queued</span>
      <span class="queue-preview">{{ nextQueuedPreview }}</span>
      <button
        v-if="!sending"
        class="queue-action"
        type="button"
        title="Send next queued draft"
        @click="flushNextQueuedDraft"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <polygon points="8 5 19 12 8 19 8 5" />
        </svg>
      </button>
      <button
        class="queue-action"
        type="button"
        title="Drop next queued draft"
        @click="dropQueuedDraft"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" />
        </svg>
      </button>
    </div>

    <div class="input-wrap">
      <div
        v-if="mentionedFiles.length || stash.length"
        class="context-row"
      >
        <span
          v-for="file in mentionedFiles"
          :key="file"
          class="context-chip"
          :title="file"
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
          {{ file }}
        </span>
        <button
          v-if="stash.length"
          class="context-chip chip-button"
          type="button"
          title="Open stashed drafts"
          @pointerdown.prevent.stop="toggleStash"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M21 8v13H3V8" /><path d="M1 3h22v5H1z" /><path d="M10 12h4" />
          </svg>
          {{ stash.length }} stash
        </button>
      </div>

      <textarea
        ref="textarea"
        v-model="text"
        :placeholder="placeholderText"
        rows="1"
        spellcheck="false"
        @paste="onPaste"
        @keydown="onKeydown"
        @input="onInput"
        @focus="updateInlinePanel"
      />
      <div
        v-if="attachments.length"
        class="attachment-row"
      >
        <div
          v-for="image in attachments"
          :key="image.id"
          class="attachment-chip"
          :title="`${image.name} (${formatSize(image.size)})`"
        >
          <img
            :src="image.dataUrl"
            :alt="image.name"
          >
          <span class="attachment-name">{{ image.name }}</span>
          <span class="attachment-size">{{ formatSize(image.size) }}</span>
          <button
            class="attachment-remove"
            type="button"
            title="Remove image"
            @click="removeAttachment(image.id)"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M18 6 6 18" /><path d="m6 6 12 12" />
            </svg>
          </button>
        </div>
      </div>
      <input
        ref="imageInput"
        class="attachment-input"
        type="file"
        :accept="IMAGE_ACCEPT"
        multiple
        @change="onImageSelected"
      >
      <div class="input-toolbar">
        <div class="tools-left">
          <ModelSelector />
          <div
            ref="modePicker"
            class="mode-picker"
            aria-label="Agent mode"
          >
            <button
              class="mode-current"
              type="button"
              :title="`Mode: ${currentModeLabel}`"
              :disabled="modePending"
              @pointerdown.prevent.stop="toggleModeMenu"
            >
              {{ currentModeLabel }}
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="m18 15-6-6-6 6" />
              </svg>
            </button>
            <transition name="mode-menu">
              <div
                v-if="modeMenuOpen"
                class="mode-menu"
              >
                <button
                  v-for="mode in modeOptions"
                  :key="mode"
                  :class="['mode-menu-item', { active: currentMode === mode }]"
                  type="button"
                  @pointerdown.prevent.stop="selectMode(mode)"
                >
                  {{ mode.toUpperCase() }}
                </button>
              </div>
            </transition>
          </div>
          <span
            v-if="sending"
            class="state-pill"
          >drafting next</span>
        </div>
        <div class="tools-right">
          <button
            class="icon-btn"
            type="button"
            title="Command palette (Ctrl+K)"
            @pointerdown.prevent.stop="togglePalette"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M4 7h16" /><path d="M4 12h10" /><path d="M4 17h7" />
            </svg>
          </button>
          <button
            class="icon-btn"
            type="button"
            title="Search history (Ctrl+H)"
            @pointerdown.prevent.stop="toggleHistory"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 3v6h6" /><path d="M12 7v5l3 2" />
            </svg>
          </button>
          <button
            class="icon-btn"
            type="button"
            title="Stash draft (Ctrl+S)"
            :disabled="!text.trim()"
            @click="stashCurrentDraft"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M21 8v13H3V8" /><path d="M1 3h22v5H1z" /><path d="M10 12h4" />
            </svg>
          </button>
          <button
            class="icon-btn"
            :class="{ active: attachments.length }"
            type="button"
            :title="attachments.length ? `${attachments.length} image attached` : 'Attach image'"
            @click="openImagePicker"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <rect
                x="3"
                y="5"
                width="18"
                height="14"
                rx="2"
              /><circle
                cx="8.5"
                cy="10.5"
                r="1.5"
              /><path d="M21 15l-5-5L5 21" />
            </svg>
          </button>
          <button
            v-if="sending && !hasDraft"
            class="btn-stop"
            type="button"
            title="Stop (Escape)"
            @click="cancelCurrent"
          >
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
            >
              <rect
                x="7"
                y="7"
                width="10"
                height="10"
                rx="1.5"
              />
            </svg>
          </button>
          <button
            v-else
            class="btn-send"
            :class="{ queued: sending }"
            :disabled="!hasDraft"
            type="button"
            :title="sending ? 'Queue draft' : 'Send'"
            @click="submitDraft"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
            >
              <path d="M12 19V5" /><path d="M5 12l7-7 7 7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useApi } from '@/composables/useApi.js'
import ModelSelector from './ModelSelector.vue'

const HISTORY_KEY = 'atri.composerHistory.v1'
const STASH_KEY = 'atri.composerStash.v1'
const MAX_HISTORY_ENTRIES = 1000
const MAX_STASH_ENTRIES = 200
const MAX_FILE_INDEX = 500
const MAX_FILE_DIRS = 80
const MAX_FILE_DEPTH = 5
const MAX_IMAGE_ATTACHMENTS = 4
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif'
const IMAGE_TYPES = new Set(IMAGE_ACCEPT.split(','))

const props = defineProps({
  sending: { type: Boolean, default: false },
  agentMode: { type: String, default: 'agent' },
  modePending: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'cancel', 'set-mode'])
const api = useApi()

const text = ref('')
const textarea = ref(null)
const imageInput = ref(null)
const popover = ref(null)
const modePicker = ref(null)
const panelSearch = ref(null)
const activePanel = ref('')
const panelQuery = ref('')
const selectedIndex = ref(0)
const modeMenuOpen = ref(false)
const statusMessage = ref('')
const queuedDrafts = ref([])
const history = ref([])
const stash = ref([])
const attachments = ref([])
const fileIndex = ref([])
const filesLoading = ref(false)
const fileIndexLoaded = ref(false)
const inlineTrigger = ref(null)
const dismissedInlineKey = ref('')
const suppressInlinePanel = ref(false)
const holdQueueAfterCancel = ref(false)
let statusTimer = null

const slashCommands = [
  {
    id: 'cmd-plan',
    label: '/plan',
    description: 'Switch to PLAN mode',
    action: 'setPlanMode',
    section: 'Mode',
  },
  {
    id: 'cmd-agent',
    label: '/agent',
    description: 'Switch to AGENT mode',
    action: 'setAgentMode',
    section: 'Mode',
  },
  {
    id: 'cmd-mode',
    label: '/mode',
    description: 'Show current mode',
    action: 'showMode',
    section: 'Mode',
  },
  {
    id: 'cmd-review',
    label: '/review',
    description: 'Review code for bugs, risk, and missing tests',
    insert: '/review ',
    section: 'Command',
  },
  {
    id: 'cmd-fix',
    label: '/fix',
    description: 'Ask ATRI to diagnose and patch a defect',
    insert: '/fix ',
    section: 'Command',
  },
  {
    id: 'cmd-explain',
    label: '/explain',
    description: 'Explain the selected problem or referenced files',
    insert: '/explain ',
    section: 'Command',
  },
  {
    id: 'cmd-test',
    label: '/test',
    description: 'Run or design verification for this change',
    insert: '/test ',
    section: 'Command',
  },
  {
    id: 'cmd-search',
    label: '/search',
    description: 'Search the workspace or ask for source lookup',
    insert: '/search ',
    section: 'Command',
  },
  {
    id: 'cmd-skill',
    label: '/skill',
    description: 'Invoke or inspect a skill by name',
    insert: '/skill ',
    section: 'Command',
  },
  {
    id: 'cmd-mcp',
    label: '/mcp',
    description: 'Ask about MCP servers, tools, or resources',
    insert: '/mcp ',
    section: 'Command',
  },
  {
    id: 'cmd-history',
    label: '/history',
    description: 'Search previous composer submissions',
    action: 'openHistory',
    section: 'Drafts',
  },
  {
    id: 'cmd-stash',
    label: '/stash',
    description: 'Open parked drafts',
    action: 'openStash',
    section: 'Drafts',
  },
  {
    id: 'cmd-queue',
    label: '/queue',
    description: 'Show queued drafts waiting for send',
    action: 'showQueue',
    section: 'Drafts',
  },
  {
    id: 'cmd-clear',
    label: '/clear',
    description: 'Clear the current composer draft',
    action: 'clearDraft',
    section: 'Drafts',
  },
]

const paletteUtilities = computed(() => [
  {
    id: 'utility-stash-current',
    label: 'Stash current draft',
    description: 'Park this composer text for later',
    action: 'stashDraft',
    section: 'Drafts',
    meta: text.value.trim() ? 'ready' : 'empty',
  },
  {
    id: 'utility-restore-stash',
    label: 'Restore latest stash',
    description: 'Pop the newest parked draft into the composer',
    action: 'restoreLatestStash',
    section: 'Drafts',
    meta: `${stash.value.length} saved`,
  },
  {
    id: 'utility-history',
    label: 'Search history',
    description: 'Find a previous prompt and reuse it',
    action: 'openHistory',
    section: 'Drafts',
    meta: `${history.value.length} entries`,
  },
  {
    id: 'utility-file-index',
    label: 'Refresh file references',
    description: 'Re-index workspace files for @ mentions',
    action: 'refreshFiles',
    section: 'Files',
    meta: fileIndexLoaded.value ? `${fileIndex.value.length} files` : 'lazy',
  },
])

const placeholderText = computed(() => (
  props.sending ? 'Draft the next message while ATRI is responding...' : 'Send a message...'
))

const currentMode = computed(() => (props.agentMode === 'plan' ? 'plan' : 'agent'))
const currentModeLabel = computed(() => currentMode.value.toUpperCase())
const modeOptions = ['plan', 'agent']

const hasDraft = computed(() => Boolean(text.value.trim() || attachments.value.length))

const hasPanelSearch = computed(() => ['palette', 'history', 'stash'].includes(activePanel.value))

const panelTitle = computed(() => {
  if (activePanel.value === 'slash') return 'Slash Commands'
  if (activePanel.value === 'file') return 'File References'
  if (activePanel.value === 'palette') return 'Command Palette'
  if (activePanel.value === 'history') return 'Composer History'
  if (activePanel.value === 'stash') return 'Draft Stash'
  return ''
})

const panelSubtitle = computed(() => {
  if (activePanel.value === 'slash') return 'Filter from the leading / command'
  if (activePanel.value === 'file') return 'Insert a workspace path as @file context'
  if (activePanel.value === 'palette') return 'Commands, draft actions, and file utilities'
  if (activePanel.value === 'history') return 'Previous sent drafts, newest first'
  if (activePanel.value === 'stash') return 'Parked drafts, newest first'
  return ''
})

const panelMeta = computed(() => {
  if (activePanel.value === 'file') {
    if (filesLoading.value) return 'indexing'
    if (fileIndexLoaded.value) return `${fileIndex.value.length} files`
  }
  if (activePanel.value === 'history') return `${history.value.length} saved`
  if (activePanel.value === 'stash') return `${stash.value.length} parked`
  if (activePanel.value === 'palette') return `${panelItems.value.length} matches`
  return ''
})

const panelSearchPlaceholder = computed(() => {
  if (activePanel.value === 'history') return 'Search sent prompts...'
  if (activePanel.value === 'stash') return 'Search stashed drafts...'
  return 'Search commands...'
})

const mentionedFiles = computed(() => {
  const seen = new Set()
  for (const match of text.value.matchAll(/(^|\s)@([^\s]+)/g)) {
    seen.add(match[2])
  }
  return Array.from(seen).slice(0, 4)
})

const panelItems = computed(() => {
  if (activePanel.value === 'slash') {
    const query = inlineTrigger.value?.query || ''
    return filterItems(slashCommands, query).slice(0, 9)
  }

  if (activePanel.value === 'file') {
    const query = inlineTrigger.value?.query || ''
    return filterItems(fileIndex.value, query).slice(0, 10)
  }

  if (activePanel.value === 'palette') {
    const entries = [
      ...slashCommands,
      ...paletteUtilities.value,
    ].map((item) => ({
      ...item,
      meta: item.meta || item.section,
    }))
    return filterItems(entries, panelQuery.value).slice(0, 12)
  }

  if (activePanel.value === 'history') {
    return filterItems(
      history.value.slice().reverse().map((entry, index) => ({
        id: `history-${index}-${hashText(entry)}`,
        label: firstLine(entry),
        description: compactText(entry),
        meta: 'history',
        text: entry,
      })),
      panelQuery.value,
    ).slice(0, 12)
  }

  if (activePanel.value === 'stash') {
    return filterItems(
      stash.value.slice().reverse().map((entry, index) => ({
        id: entry.id || `stash-${index}-${hashText(entry.text)}`,
        label: firstLine(entry.text),
        description: compactText(entry.text),
        meta: formatDraftTime(entry.ts),
        text: entry.text,
        stashIndex: stash.value.length - 1 - index,
      })),
      panelQuery.value,
    ).slice(0, 12)
  }

  return []
})

const nextQueuedPreview = computed(() => {
  const next = queuedDrafts.value[0]
  if (!next) return ''
  const draft = compactText(next.text || '')
  const count = next.images?.length || 0
  if (!count) return draft
  return `${draft || '(image only)'} · ${formatImageCount(count)}`
})

const panelEmptyText = computed(() => {
  if (activePanel.value === 'file' && filesLoading.value) return 'Indexing workspace files...'
  if (activePanel.value === 'file' && !fileIndexLoaded.value) return 'Type @ to load workspace files'
  if (activePanel.value === 'history') return 'No matching history'
  if (activePanel.value === 'stash') return 'No stashed drafts'
  return 'No matches'
})

watch(panelItems, (items) => {
  if (selectedIndex.value >= items.length) {
    selectedIndex.value = Math.max(0, items.length - 1)
  }
})

watch(() => props.sending, (sending, wasSending) => {
  if (wasSending && !sending) {
    if (holdQueueAfterCancel.value) {
      holdQueueAfterCancel.value = false
      if (queuedDrafts.value.length) {
        showStatus('Queue paused after cancel')
      }
      return
    }
    nextTick(flushNextQueuedDraft)
  }
})

function readJson(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return fallback
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : fallback
  } catch {
    return fallback
  }
}

function writeJson(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Composer persistence is best-effort.
  }
}

function normalizeHistoryEntry(entry) {
  return typeof entry === 'string' ? entry : String(entry?.text || '')
}

function normalizeStashEntry(entry) {
  if (typeof entry === 'string') {
    return {
      id: makeId(),
      ts: Date.now(),
      text: entry,
    }
  }
  return {
    id: entry?.id || makeId(),
    ts: entry?.ts || Date.now(),
    text: String(entry?.text || ''),
  }
}

function persistHistory() {
  writeJson(HISTORY_KEY, history.value.slice(-MAX_HISTORY_ENTRIES))
}

function persistStash() {
  writeJson(STASH_KEY, stash.value.slice(-MAX_STASH_ENTRIES))
}

function pushHistory(entry) {
  const trimmed = entry.trim()
  if (!trimmed || trimmed.startsWith('/')) return
  if (history.value[history.value.length - 1] === trimmed) return
  history.value.push(trimmed)
  if (history.value.length > MAX_HISTORY_ENTRIES) {
    history.value.splice(0, history.value.length - MAX_HISTORY_ENTRIES)
  }
  persistHistory()
}

function makeId() {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

function hashText(value) {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0
  }
  return Math.abs(hash).toString(36)
}

function firstLine(value) {
  const line = String(value || '').trim().split(/\r?\n/)[0] || '(empty draft)'
  return line.length > 72 ? `${line.slice(0, 69)}...` : line
}

function compactText(value) {
  const compact = String(value || '').replace(/\s+/g, ' ').trim()
  return compact.length > 110 ? `${compact.slice(0, 107)}...` : compact
}

function formatDraftTime(ts) {
  if (!ts) return 'stash'
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return 'stash'
  return date.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatImageCount(count) {
  return `${count} image${count === 1 ? '' : 's'}`
}

function openImagePicker() {
  imageInput.value?.click()
}

function onImageSelected(event) {
  addImageFiles(event.target?.files)
}

function onPaste(event) {
  const files = Array.from(event.clipboardData?.files || [])
    .filter((file) => file.type?.startsWith('image/'))
  if (!files.length) return
  event.preventDefault()
  addImageFiles(files)
}

async function addImageFiles(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return

  const slots = MAX_IMAGE_ATTACHMENTS - attachments.value.length
  if (slots <= 0) {
    showStatus(`Limit ${MAX_IMAGE_ATTACHMENTS} images`)
    resetImageInput()
    return
  }

  let added = 0
  for (const file of files.slice(0, slots)) {
    if (!IMAGE_TYPES.has(file.type)) {
      showStatus('Use PNG, JPEG, WebP, or GIF images')
      continue
    }
    if (file.size > MAX_IMAGE_BYTES) {
      showStatus(`Image must be ${formatSize(MAX_IMAGE_BYTES)} or smaller`)
      continue
    }
    try {
      const dataUrl = await readImageFile(file)
      attachments.value.push({
        id: makeId(),
        name: file.name || `image-${attachments.value.length + 1}`,
        type: file.type,
        size: file.size,
        dataUrl,
      })
      added += 1
    } catch {
      showStatus('Unable to read image')
    }
  }

  if (files.length > slots) {
    showStatus(`Attached ${added}, limit ${MAX_IMAGE_ATTACHMENTS}`)
  } else if (added) {
    showStatus(`Attached ${formatImageCount(added)}`)
  }
  resetImageInput()
}

function readImageFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

function removeAttachment(id) {
  attachments.value = attachments.value.filter((image) => image.id !== id)
  resetImageInput()
}

function resetImageInput() {
  if (imageInput.value) {
    imageInput.value.value = ''
  }
}

function clearAttachments() {
  attachments.value = []
  resetImageInput()
}

function cloneAttachments(images = attachments.value) {
  return images.map((image) => ({
    id: image.id,
    name: image.name,
    type: image.type,
    size: image.size,
    dataUrl: image.dataUrl,
  }))
}

function filterItems(items, query) {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return items

  return items
    .map((item, order) => {
      const haystack = [
        item.label,
        item.description,
        item.meta,
        item.insert,
        item.text,
      ].filter(Boolean).join(' ').toLowerCase()
      if (!terms.every((term) => haystack.includes(term))) return null
      const label = String(item.label || '').toLowerCase()
      const score = terms.reduce((total, term) => {
        if (label === term) return total
        if (label.startsWith(term)) return total + 1
        if (label.includes(term)) return total + 2
        return total + 4
      }, 0)
      return { item, score, order }
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score || a.order - b.order)
    .map(({ item }) => item)
}

function showStatus(message) {
  statusMessage.value = message
  if (statusTimer) window.clearTimeout(statusTimer)
  statusTimer = window.setTimeout(() => {
    statusMessage.value = ''
  }, 2200)
}

function autoResize() {
  nextTick(() => {
    const el = textarea.value
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  })
}

function onInput() {
  dismissedInlineKey.value = ''
  suppressInlinePanel.value = false
  autoResize()
  updateInlinePanel()
}

function currentCursor() {
  return textarea.value?.selectionStart ?? text.value.length
}

function detectInlineTrigger() {
  const cursor = currentCursor()
  const before = text.value.slice(0, cursor)
  const slashMatch = before.match(/^\/([^\s]*)$/)
  if (slashMatch) {
    return {
      kind: 'slash',
      query: slashMatch[1],
      start: 0,
      end: cursor,
    }
  }

  const fileMatch = before.match(/(^|\s)@([^\s@]*)$/)
  if (fileMatch) {
    return {
      kind: 'file',
      query: fileMatch[2],
      start: cursor - fileMatch[2].length - 1,
      end: cursor,
    }
  }

  return null
}

function getInlineKey(trigger) {
  if (!trigger) return ''
  return `${trigger.kind}:${trigger.start}:${trigger.query}`
}

function updateInlinePanel() {
  if (hasPanelSearch.value) return
  const trigger = detectInlineTrigger()
  inlineTrigger.value = trigger
  if (suppressInlinePanel.value) {
    if (activePanel.value === 'slash' || activePanel.value === 'file') {
      closePanel({ focus: false })
    }
    return
  }
  if (!trigger || dismissedInlineKey.value === getInlineKey(trigger)) {
    if (activePanel.value === 'slash' || activePanel.value === 'file') {
      closePanel({ focus: false })
    }
    return
  }
  activePanel.value = trigger.kind
  selectedIndex.value = 0
  if (trigger.kind === 'file') {
    ensureFileIndex()
  }
}

function onKeydown(e) {
  if (handlePanelNavigation(e)) return

  const key = e.key.toLowerCase()
  if ((e.ctrlKey || e.metaKey) && key === 'k') {
    e.preventDefault()
    togglePalette()
    return
  }
  if ((e.ctrlKey || e.metaKey) && key === 'h') {
    e.preventDefault()
    toggleHistory()
    return
  }
  if ((e.ctrlKey || e.metaKey) && key === 's') {
    e.preventDefault()
    if (e.shiftKey) {
      toggleStash()
    } else {
      stashCurrentDraft()
    }
    return
  }
  if (e.key === 'ArrowUp' && isAtComposerStart() && !text.value && history.value.length) {
    e.preventDefault()
    openHistory()
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitDraft()
  }
}

function onPanelSearchKeydown(e) {
  handlePanelNavigation(e)
}

function handlePanelNavigation(e) {
  if (!activePanel.value) return false
  const count = panelItems.value.length

  if (e.key === 'Escape') {
    e.preventDefault()
    if (inlineTrigger.value) {
      dismissedInlineKey.value = getInlineKey(inlineTrigger.value)
    }
    closePanel()
    return true
  }

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    moveSelection(1)
    return true
  }

  if (e.key === 'ArrowUp') {
    e.preventDefault()
    moveSelection(-1)
    return true
  }

  if (e.key === 'PageDown') {
    e.preventDefault()
    moveSelection(5)
    return true
  }

  if (e.key === 'PageUp') {
    e.preventDefault()
    moveSelection(-5)
    return true
  }

  if ((e.key === 'Enter' || e.key === 'Tab') && count) {
    e.preventDefault()
    applyPanelItem(selectedIndex.value)
    return true
  }

  return false
}

function moveSelection(delta) {
  const count = panelItems.value.length
  if (!count) {
    selectedIndex.value = 0
    return
  }
  selectedIndex.value = Math.min(count - 1, Math.max(0, selectedIndex.value + delta))
}

function isAtComposerStart() {
  const el = textarea.value
  if (!el) return false
  return el.selectionStart === 0 && el.selectionEnd === 0
}

function focusTextarea() {
  nextTick(() => textarea.value?.focus())
}

function focusSearch() {
  nextTick(() => panelSearch.value?.focus())
}

function openPalette() {
  modeMenuOpen.value = false
  activePanel.value = 'palette'
  panelQuery.value = ''
  selectedIndex.value = 0
  inlineTrigger.value = null
  suppressInlinePanel.value = true
  focusSearch()
}

function togglePalette() {
  if (activePanel.value === 'palette') {
    closePanel()
    return
  }
  openPalette()
}

function openHistory() {
  modeMenuOpen.value = false
  activePanel.value = 'history'
  panelQuery.value = ''
  selectedIndex.value = 0
  inlineTrigger.value = null
  suppressInlinePanel.value = true
  focusSearch()
}

function toggleHistory() {
  if (activePanel.value === 'history') {
    closePanel()
    return
  }
  openHistory()
}

function openStash() {
  modeMenuOpen.value = false
  activePanel.value = 'stash'
  panelQuery.value = ''
  selectedIndex.value = 0
  inlineTrigger.value = null
  suppressInlinePanel.value = true
  focusSearch()
}

function toggleStash() {
  if (activePanel.value === 'stash') {
    closePanel()
    return
  }
  openStash()
}

function closePanel({ focus = true } = {}) {
  suppressInlinePanel.value = true
  activePanel.value = ''
  panelQuery.value = ''
  selectedIndex.value = 0
  if (focus) focusTextarea()
}

function toggleModeMenu() {
  closePanel({ focus: false })
  modeMenuOpen.value = !modeMenuOpen.value
}

function selectMode(mode) {
  modeMenuOpen.value = false
  requestMode(mode)
}

function closePanelFromOutside() {
  const trigger = inlineTrigger.value || detectInlineTrigger()
  if (trigger) {
    dismissedInlineKey.value = getInlineKey(trigger)
  }
  suppressInlinePanel.value = true
  closePanel({ focus: false })
}

function onDocumentPointerdown(e) {
  const target = e.target
  if (!(target instanceof Node)) return
  if (modeMenuOpen.value && !modePicker.value?.contains(target)) {
    modeMenuOpen.value = false
  }
  if (!activePanel.value) return
  if (popover.value?.contains(target)) return
  closePanelFromOutside()
}

function applyPanelItem(index) {
  const item = panelItems.value[index]
  if (!item) return

  if (activePanel.value === 'file') {
    replaceInlineTrigger(`@${item.path} `)
    showStatus(`Referenced ${item.path}`)
    closePanel()
    return
  }

  if (activePanel.value === 'history') {
    setComposerText(item.text || '')
    closePanel()
    return
  }

  if (activePanel.value === 'stash') {
    restoreStash(item.stashIndex)
    closePanel()
    return
  }

  if (item.action) {
    runPanelAction(item.action)
    return
  }

  if (activePanel.value === 'slash') {
    setComposerText(item.insert || item.label)
    closePanel()
    return
  }

  insertAtCursor(item.insert || item.label || '')
  closePanel()
}

function runPanelAction(action) {
  if (action === 'openHistory') {
    openHistory()
  } else if (action === 'openStash') {
    openStash()
  } else if (action === 'showQueue') {
    showStatus(queuedDrafts.value.length ? `${queuedDrafts.value.length} drafts queued` : 'Queue is empty')
    closePanel()
  } else if (action === 'clearDraft') {
    setComposerText('')
    showStatus('Draft cleared')
    closePanel()
  } else if (action === 'stashDraft') {
    stashCurrentDraft()
    closePanel()
  } else if (action === 'restoreLatestStash') {
    restoreStash(stash.value.length - 1)
    closePanel()
  } else if (action === 'refreshFiles') {
    refreshFileIndex()
    closePanel()
  } else if (action === 'setPlanMode') {
    requestMode('plan')
    closePanel()
  } else if (action === 'setAgentMode') {
    requestMode('agent')
    closePanel()
  } else if (action === 'showMode') {
    showStatus(`Mode: ${currentMode.value.toUpperCase()}`)
    closePanel()
  }
}

function requestMode(mode) {
  const nextMode = mode === 'plan' ? 'plan' : 'agent'
  modeMenuOpen.value = false
  closePanel({ focus: false })
  emit('set-mode', nextMode)
  showStatus(`Mode: ${nextMode.toUpperCase()}`)
}

function setComposerText(value) {
  text.value = value
  nextTick(() => {
    autoResize()
    const el = textarea.value
    if (!el) return
    el.focus()
    el.setSelectionRange(value.length, value.length)
    updateInlinePanel()
  })
}

function insertAtCursor(fragment) {
  const el = textarea.value
  const start = el?.selectionStart ?? text.value.length
  const end = el?.selectionEnd ?? start
  const next = `${text.value.slice(0, start)}${fragment}${text.value.slice(end)}`
  text.value = next
  nextTick(() => {
    autoResize()
    textarea.value?.focus()
    textarea.value?.setSelectionRange(start + fragment.length, start + fragment.length)
    updateInlinePanel()
  })
}

function replaceInlineTrigger(replacement) {
  const trigger = inlineTrigger.value
  if (!trigger) {
    insertAtCursor(replacement)
    return
  }
  const next = `${text.value.slice(0, trigger.start)}${replacement}${text.value.slice(trigger.end)}`
  const cursor = trigger.start + replacement.length
  text.value = next
  nextTick(() => {
    autoResize()
    textarea.value?.focus()
    textarea.value?.setSelectionRange(cursor, cursor)
    updateInlinePanel()
  })
}

function submitDraft() {
  const draft = text.value.trim()
  const images = cloneAttachments()
  if (!draft && !images.length) return
  closePanel({ focus: false })
  if (!images.length && runComposerCommand(draft)) {
    return
  }
  if (props.sending) {
    queueDraft(text.value, images)
    setComposerText('')
    clearAttachments()
    return
  }
  dispatchDraft(text.value, images)
  setComposerText('')
  clearAttachments()
}

function runComposerCommand(draft) {
  const command = draft.trim().toLowerCase()
  if (command === '/plan') {
    requestMode('plan')
    setComposerText('')
    return true
  }
  if (command === '/agent') {
    requestMode('agent')
    setComposerText('')
    return true
  }
  if (command === '/mode') {
    showStatus(`Mode: ${currentMode.value.toUpperCase()}`)
    setComposerText('')
    return true
  }
  if (command === '/history') {
    clearComposerWithoutFocus()
    openHistory()
    return true
  }
  if (command === '/stash') {
    clearComposerWithoutFocus()
    openStash()
    return true
  }
  if (command === '/queue') {
    showStatus(queuedDrafts.value.length ? `${queuedDrafts.value.length} drafts queued` : 'Queue is empty')
    setComposerText('')
    return true
  }
  if (command === '/clear') {
    showStatus('Draft cleared')
    setComposerText('')
    clearAttachments()
    return true
  }
  return false
}

function clearComposerWithoutFocus() {
  text.value = ''
  autoResize()
}

function queueDraft(draft, images = []) {
  queuedDrafts.value.push({
    id: makeId(),
    text: draft,
    images,
    ts: Date.now(),
  })
  showStatus(`Queued draft ${queuedDrafts.value.length}`)
}

function dispatchDraft(draft, images = []) {
  const value = draft.trim()
  if (!value && !images.length) return
  if (value) pushHistory(value)
  emit('send', { text: draft, images })
}

function flushNextQueuedDraft() {
  if (props.sending || !queuedDrafts.value.length) return
  const next = queuedDrafts.value.shift()
  if (!next) return
  dispatchDraft(next.text || '', next.images || [])
  showStatus(queuedDrafts.value.length ? `Sent queued draft, ${queuedDrafts.value.length} left` : 'Sent queued draft')
}

function dropQueuedDraft() {
  if (!queuedDrafts.value.length) return
  queuedDrafts.value.shift()
  showStatus(queuedDrafts.value.length ? `${queuedDrafts.value.length} drafts remain` : 'Queue cleared')
}

function stashCurrentDraft() {
  const draft = text.value
  if (!draft.trim()) return
  stash.value.push({
    id: makeId(),
    ts: Date.now(),
    text: draft,
  })
  if (stash.value.length > MAX_STASH_ENTRIES) {
    stash.value.splice(0, stash.value.length - MAX_STASH_ENTRIES)
  }
  persistStash()
  setComposerText('')
  showStatus('Draft stashed')
}

function restoreStash(index) {
  if (index < 0 || index >= stash.value.length) {
    showStatus('No stashed drafts')
    return
  }
  const [draft] = stash.value.splice(index, 1)
  persistStash()
  setComposerText(draft.text)
  showStatus('Draft restored')
}

function cancelCurrent() {
  holdQueueAfterCancel.value = true
  emit('cancel')
}

async function ensureFileIndex() {
  if (fileIndexLoaded.value || filesLoading.value) return
  await loadFileIndex()
}

async function refreshFileIndex() {
  fileIndexLoaded.value = false
  fileIndex.value = []
  await loadFileIndex()
  showStatus(`Indexed ${fileIndex.value.length} files`)
}

async function loadFileIndex() {
  filesLoading.value = true
  const files = []
  const dirs = [{ path: '', depth: 0 }]
  let visitedDirs = 0

  try {
    while (dirs.length && files.length < MAX_FILE_INDEX && visitedDirs < MAX_FILE_DIRS) {
      const current = dirs.shift()
      visitedDirs += 1
      const data = await api.listFiles(current.path)
      for (const entry of data.entries || []) {
        if (entry.type === 'dir') {
          if (current.depth < MAX_FILE_DEPTH) {
            dirs.push({ path: entry.path, depth: current.depth + 1 })
          }
          continue
        }
        files.push({
          id: `file-${entry.path}`,
          label: entry.path,
          description: entry.name,
          meta: formatSize(entry.size),
          path: entry.path,
        })
        if (files.length >= MAX_FILE_INDEX) break
      }
    }
    fileIndex.value = files
    fileIndexLoaded.value = true
  } catch {
    fileIndex.value = []
    showStatus('Unable to index workspace files')
  } finally {
    filesLoading.value = false
  }
}

onMounted(() => {
  history.value = readJson(HISTORY_KEY, [])
    .map(normalizeHistoryEntry)
    .filter(Boolean)
    .slice(-MAX_HISTORY_ENTRIES)
  stash.value = readJson(STASH_KEY, [])
    .map(normalizeStashEntry)
    .filter((entry) => entry.text.trim())
    .slice(-MAX_STASH_ENTRIES)
  autoResize()
  document.addEventListener('pointerdown', onDocumentPointerdown)
})

onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocumentPointerdown)
  if (statusTimer) {
    window.clearTimeout(statusTimer)
  }
})
</script>

<style scoped>
.input-area {
  position: relative;
  padding: 12px 16px 16px;
  flex-shrink: 0;
}

.composer-popover {
  position: absolute;
  left: 50%;
  bottom: calc(100% - 10px);
  width: min(720px, calc(100% - 32px));
  max-height: min(460px, 54vh);
  display: flex;
  flex-direction: column;
  gap: 8px;
  transform: translateX(-50%);
  z-index: 20;
  background: rgba(30, 30, 30, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  padding: 10px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.42);
}

.composer-popover.is-modal-panel {
  width: min(760px, calc(100% - 32px));
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 2px 0;
}

.panel-title {
  color: var(--t1);
  font-size: 13px;
  font-weight: 700;
}

.panel-subtitle,
.panel-meta {
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.panel-meta {
  flex-shrink: 0;
  padding-top: 1px;
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.panel-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
}

.panel-close:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--t1);
}

.panel-close svg {
  width: 14px;
  height: 14px;
}

.panel-search {
  width: 100%;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg1);
  color: var(--t1);
  font-family: var(--mono);
  font-size: 12px;
  padding: 0 10px;
  outline: none;
}

.panel-search:focus {
  border-color: rgba(55, 148, 255, 0.55);
  box-shadow: 0 0 0 1px rgba(55, 148, 255, 0.16);
}

.panel-list {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.panel-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--t2);
  padding: 7px 9px;
  cursor: pointer;
  text-align: left;
}

.panel-item.selected,
.panel-item:hover {
  background: rgba(55, 148, 255, 0.13);
  border-color: rgba(55, 148, 255, 0.24);
  color: var(--t1);
}

.item-main {
  min-width: 0;
  display: grid;
  gap: 1px;
}

.item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: inherit;
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 700;
}

.item-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t3);
  font-size: 12px;
}

.item-meta {
  flex-shrink: 0;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.panel-empty {
  padding: 18px 8px;
  color: var(--t3);
  font-size: 12px;
  text-align: center;
}

.panel-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding: 8px 2px 0;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
}

.composer-status,
.draft-queue {
  max-width: 920px;
  margin: 0 auto 8px;
}

.composer-status {
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.draft-queue {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  border: 1px solid rgba(55, 148, 255, 0.24);
  border-radius: 8px;
  background: rgba(55, 148, 255, 0.1);
  color: var(--t2);
  padding: 5px 7px;
}

.queue-count {
  min-width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--acc2);
  color: #101010;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
}

.queue-label {
  color: var(--t1);
  font-size: 12px;
  font-weight: 700;
}

.queue-preview {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t3);
  font-size: 12px;
}

.queue-action {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
}

.queue-action:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--t1);
}

.queue-action svg {
  width: 13px;
  height: 13px;
}

.input-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 920px;
  margin: 0 auto;
  background: rgba(37, 37, 38, 0.92);
  border: 1px solid var(--border-input);
  border-radius: 13px;
  padding: 12px 12px 10px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.04);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.input-wrap:focus-within {
  border-color: rgba(255, 255, 255, 0.22);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.03);
}

.context-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow-x: auto;
  padding-bottom: 1px;
}

.context-chip {
  max-width: 220px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  border: 1px solid rgba(55, 148, 255, 0.26);
  border-radius: 999px;
  background: rgba(55, 148, 255, 0.1);
  color: var(--t2);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1;
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-button {
  cursor: pointer;
}

.chip-button:hover {
  color: var(--t1);
  background: rgba(55, 148, 255, 0.16);
}

.context-chip svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}

.attachment-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow-x: auto;
  padding: 1px 0 2px;
}

.attachment-chip {
  width: 188px;
  min-width: 188px;
  height: 54px;
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) 24px;
  grid-template-rows: 1fr 1fr;
  align-items: center;
  gap: 2px 8px;
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 8px;
  background: rgba(20, 20, 20, 0.56);
  padding: 6px;
}

.attachment-chip img {
  grid-row: 1 / 3;
  width: 42px;
  height: 42px;
  border-radius: 6px;
  object-fit: cover;
  background: rgba(255, 255, 255, 0.05);
}

.attachment-name,
.attachment-size {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-name {
  align-self: end;
  color: var(--t2);
  font-size: 12px;
  font-weight: 600;
}

.attachment-size {
  align-self: start;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
}

.attachment-remove {
  grid-column: 3;
  grid-row: 1 / 3;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
}

.attachment-remove:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--t1);
}

.attachment-remove svg {
  width: 13px;
  height: 13px;
}

.attachment-input {
  display: none;
}

textarea {
  width: 100%;
  background: transparent;
  border: none;
  color: var(--t1);
  padding: 0 2px;
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  resize: none;
  min-height: 28px;
  max-height: 180px;
  outline: none;
}

textarea::placeholder {
  color: var(--t3);
}

.input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tools-left,
.tools-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tools-left {
  min-width: 0;
}

.mode-picker {
  position: relative;
  flex-shrink: 0;
}

.mode-current {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: rgba(133, 133, 133, 0.82);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0;
  padding: 0 7px;
  cursor: pointer;
}

.mode-current:hover,
.mode-current:focus-visible {
  background: rgba(255, 255, 255, 0.05);
  color: var(--t2);
  border-color: rgba(255, 255, 255, 0.08);
}

.mode-current svg {
  width: 11px;
  height: 11px;
  opacity: 0.65;
}

.mode-current:disabled {
  opacity: 0.55;
  cursor: wait;
}

.mode-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 30;
  min-width: 92px;
  display: grid;
  gap: 2px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 7px;
  background: rgba(30, 30, 30, 0.98);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.34);
  padding: 4px;
}

.mode-menu-item {
  height: 28px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  text-align: left;
  padding: 0 8px;
  cursor: pointer;
}

.mode-menu-item:hover,
.mode-menu-item.active {
  background: rgba(55, 148, 255, 0.13);
  color: var(--t1);
}

.mode-menu-enter-active,
.mode-menu-leave-active {
  transition: opacity 0.1s ease, transform 0.1s ease;
}

.mode-menu-enter-from,
.mode-menu-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

.state-pill {
  border: 1px solid rgba(137, 209, 133, 0.22);
  border-radius: 999px;
  background: var(--green-bg);
  color: var(--green);
  font-family: var(--mono);
  font-size: 10px;
  padding: 2px 7px;
  white-space: nowrap;
}

.icon-btn {
  width: 31px;
  height: 31px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--t3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--t1);
}

.icon-btn.active {
  background: rgba(55, 148, 255, 0.13);
  color: var(--acc2);
}

.icon-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.icon-btn svg {
  width: 16px;
  height: 16px;
}

.btn-send {
  width: 32px;
  height: 32px;
  background: #e8e8e8;
  color: #1f1f1f;
  border: none;
  border-radius: 50%;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.12s, transform 0.12s;
}

.btn-send:hover {
  background: #fff;
  transform: translateY(-1px);
}

.btn-send.queued {
  background: var(--acc2);
  color: #101010;
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
}

.btn-send svg {
  width: 16px;
  height: 16px;
}

.btn-stop {
  width: 32px;
  height: 32px;
  background: #e53935;
  color: #fff;
  border: none;
  border-radius: 50%;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.12s, transform 0.12s;
  animation: stop-pulse 1.8s ease-in-out infinite;
}

.btn-stop:hover {
  background: #ff5252;
  transform: translateY(-1px);
}

.btn-stop svg {
  width: 14px;
  height: 14px;
}

.composer-pop-enter-active,
.composer-pop-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}

.composer-pop-enter-from,
.composer-pop-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(6px);
}

@keyframes stop-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(229, 57, 53, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(229, 57, 53, 0); }
}

@media (max-width: 720px) {
  .input-area {
    padding: 10px;
  }

  .composer-popover {
    width: calc(100% - 20px);
    bottom: calc(100% - 6px);
  }

  .panel-footer {
    display: none;
  }

  .state-pill {
    display: none;
  }

  .context-chip {
    max-width: 160px;
  }

  .attachment-chip {
    width: 160px;
    min-width: 160px;
  }
}
</style>
