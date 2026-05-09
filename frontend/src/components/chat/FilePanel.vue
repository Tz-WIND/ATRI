<template>
  <div class="file-panel">
    <div class="explorer-head">
      <span class="explorer-title">EXPLORER</span>
      <button
        class="head-btn"
        title="Refresh"
        :disabled="isPathLoading('')"
        @click="refresh"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <polyline points="23 4 23 10 17 10" />
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
        </svg>
      </button>
    </div>

    <div class="tree-scroll">
      <div
        v-if="isPathLoading('') && !rootEntries.length"
        class="tree-empty"
      >
        Loading...
      </div>
      <div
        v-else-if="!rootEntries.length"
        class="tree-empty"
      >
        Empty directory
      </div>
      <div
        v-else
        class="tree-list"
      >
        <button
          type="button"
          class="tree-row root-row"
          :style="{ '--depth': 0 }"
          @click="toggleRoot"
        >
          <span class="chevron open">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </span>
          <span class="entry-icon root-icon">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z" />
            </svg>
          </span>
          <span class="entry-name root-name">ATRI</span>
        </button>

        <button
          v-for="row in visibleRows"
          :key="row.entry.path"
          type="button"
          :title="row.entry.path"
          :class="[
            'tree-row',
            {
              active: selectedPath === row.entry.path,
              directory: row.entry.type === 'dir',
              file: row.entry.type === 'file',
              loading: isPathLoading(row.entry.path),
            },
          ]"
          :style="{ '--depth': row.depth }"
          @click="handleEntryClick(row.entry)"
        >
          <span
            class="chevron"
            :class="{ open: row.entry.type === 'dir' && isExpanded(row.entry.path) }"
          >
            <svg
              v-if="row.entry.type === 'dir'"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="m9 18 6-6-6-6" />
            </svg>
          </span>
          <span
            class="entry-icon"
            :class="iconClass(row.entry)"
            v-html="entryIcon(row.entry)"
          />
          <span class="entry-name">{{ row.entry.name }}</span>
          <span
            v-if="row.entry.type === 'file' && row.entry.size != null"
            class="entry-size"
          >
            {{ formatSize(row.entry.size) }}
          </span>
          <span
            v-if="row.entry.type === 'dir' && isPathLoading(row.entry.path)"
            class="row-spinner"
          />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useApi } from '@/composables/useApi.js'

const emit = defineEmits(['open-file'])
const api = useApi()

const tree = ref({ '': [] })
const expandedPaths = ref(new Set(['']))
const loadingPaths = ref(new Set())
const selectedPath = ref('')
const rootOpen = ref(true)

const rootEntries = computed(() => tree.value[''] || [])

const visibleRows = computed(() => {
  if (!rootOpen.value) return []
  const rows = []
  appendRows('', 1, rows)
  return rows
})

function appendRows(path, depth, rows) {
  const entries = tree.value[path] || []
  entries.forEach((entry) => {
    rows.push({ entry, depth })
    if (entry.type === 'dir' && expandedPaths.value.has(entry.path)) {
      appendRows(entry.path, depth + 1, rows)
    }
  })
}

function normalizeEntries(entries = []) {
  return [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    return String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' })
  })
}

function setLoading(path, loading) {
  const next = new Set(loadingPaths.value)
  if (loading) next.add(path)
  else next.delete(path)
  loadingPaths.value = next
}

function isPathLoading(path) {
  return loadingPaths.value.has(path)
}

function isExpanded(path) {
  return expandedPaths.value.has(path)
}

async function loadDir(path, force = false) {
  if (!force && tree.value[path]) return
  setLoading(path, true)
  try {
    const data = await api.listFiles(path)
    tree.value = {
      ...tree.value,
      [path]: normalizeEntries(data.entries || []),
    }
  } catch {
    tree.value = {
      ...tree.value,
      [path]: [],
    }
  } finally {
    setLoading(path, false)
  }
}

function toggleRoot() {
  rootOpen.value = !rootOpen.value
}

async function toggleDir(entry) {
  const next = new Set(expandedPaths.value)
  if (next.has(entry.path)) {
    next.delete(entry.path)
    expandedPaths.value = next
    return
  }

  next.add(entry.path)
  expandedPaths.value = next
  await loadDir(entry.path)
}

async function handleEntryClick(entry) {
  selectedPath.value = entry.path
  if (entry.type === 'dir') {
    await toggleDir(entry)
    return
  }
  emit('open-file', { path: entry.path, name: entry.name })
}

async function refresh() {
  const expanded = [...expandedPaths.value]
  await Promise.all(expanded.map(path => loadDir(path, true)))
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function extension(name) {
  const lower = String(name || '').toLowerCase()
  const idx = lower.lastIndexOf('.')
  return idx >= 0 ? lower.slice(idx + 1) : lower
}

function iconClass(entry) {
  if (entry.type === 'dir') return isExpanded(entry.path) ? 'folder open' : 'folder'
  const name = String(entry.name || '').toLowerCase()
  if (name === 'makefile') return 'makefile'
  if (name === 'license') return 'license'
  if (name === '.gitignore') return 'git'
  if (name.includes('requirements')) return 'requirements'
  return `file-${extension(name)}`
}

function entryIcon(entry) {
  if (entry.type === 'dir') {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>'
  }

  const name = String(entry.name || '').toLowerCase()
  if (extension(name) === 'py') {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3h6a4 4 0 0 1 4 4v2H8a3 3 0 0 0-3 3v1H3V9a4 4 0 0 1 4-4h1z"/><path d="M16 21h-6a4 4 0 0 1-4-4v-2h10a3 3 0 0 0 3-3v-1h2v4a4 4 0 0 1-4 4h-1z"/><circle cx="9" cy="7" r=".8"/><circle cx="15" cy="17" r=".8"/></svg>'
  }
  if (name === 'makefile') return '<span class="letter-icon">M</span>'
  if (name === 'license') return '<span class="letter-icon">A</span>'
  if (name.endsWith('.md')) return '<span class="letter-icon info">i</span>'
  if (name.endsWith('.toml')) return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.2a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.2a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3 1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.2a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8 1.6 1.6 0 0 0 1.5 1h.2a2 2 0 0 1 0 4h-.2a1.6 1.6 0 0 0-1.5 1z"/></svg>'
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z"/><path d="M14 2v5h5"/></svg>'
}

onMounted(() => loadDir('', true))
</script>

<style scoped>
.file-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  background: #181818;
}

.explorer-head {
  position: relative;
  height: 29px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  padding: 0 8px 0 10px;
  color: #8d8d8d;
}

.explorer-head::after {
  content: "";
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  height: 1px;
  background: rgba(255, 255, 255, 0.045);
}

.explorer-title {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #8a8a8a;
}

.head-btn {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #777;
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
}

.head-btn:hover:not(:disabled) {
  color: #c9c9c9;
  background: rgba(255, 255, 255, 0.055);
}

.head-btn:disabled {
  opacity: 0.45;
  cursor: wait;
}

.head-btn svg {
  width: 12px;
  height: 12px;
}

.tree-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.tree-list {
  padding: 4px 0 10px;
}

.tree-row {
  width: 100%;
  height: 23px;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 0 8px 0 calc(var(--depth) * 12px + 4px);
  border: 0;
  background: transparent;
  color: #8f8f8f;
  font-size: 12px;
  line-height: 23px;
  text-align: left;
  cursor: default;
  user-select: none;
  white-space: nowrap;
  transition: background 0.08s, color 0.08s;
}

.tree-row:hover {
  background: rgba(255, 255, 255, 0.035);
}

.tree-row.active {
  background: rgba(255, 255, 255, 0.09);
}

.tree-row.file.active .entry-name {
  color: var(--t1);
}

.tree-row.root-row {
  color: #a0a0a0;
}

.chevron {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 14px;
  color: #7b7b7b;
}

.chevron svg {
  width: 12px;
  height: 12px;
  transition: transform 0.1s;
}

.chevron.open svg {
  transform: rotate(90deg);
}

.root-row .chevron.open svg {
  transform: rotate(0deg);
}

.entry-icon {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 16px;
  color: #8c8c8c;
}

.entry-icon :deep(svg) {
  width: 15px;
  height: 15px;
}

.entry-icon.folder {
  color: #8f8f8f;
}

.entry-icon.folder.open,
.root-icon {
  color: #9a9a9a;
}

.entry-icon.file-py {
  color: var(--code-literal);
}

.tree-row.file.active .entry-icon {
  color: var(--acc2);
}

.entry-icon.file-yaml,
.entry-icon.file-yml {
  color: #b58cff;
}

.entry-icon.file-md {
  color: #42b8dd;
}

.entry-icon.file-toml {
  color: #7aa6b7;
}

.entry-icon.git {
  color: #6d8da8;
}

.entry-icon.license {
  color: #d6c558;
}

.entry-icon.makefile {
  color: #ff8f4d;
}

.entry-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  color: currentColor;
}

.root-name {
  font-weight: 600;
}

.entry-size {
  margin-left: 8px;
  flex: 0 0 auto;
  color: #666;
  font-family: var(--mono);
  font-size: 10px;
}

.letter-icon {
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 800;
  line-height: 1;
}

.letter-icon.info {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid currentColor;
  border-radius: 50%;
  font-size: 10px;
}

.row-spinner {
  width: 10px;
  height: 10px;
  flex: 0 0 10px;
  border: 1px solid #777;
  border-top-color: transparent;
  border-radius: 50%;
  animation: row-spin 0.8s linear infinite;
}

.tree-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 72px;
  color: #777;
  font-size: 12px;
}

@keyframes row-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
