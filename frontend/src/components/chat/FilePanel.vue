<template>
  <div class="file-panel">
    <div class="file-toolbar">
      <button
        class="icon-btn"
        :disabled="!currentPath"
        title="Go up"
        @click="goUp"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <div
        class="path-display"
        :title="currentPath || 'workspace root'"
      >
        {{ currentPath || '/' }}
      </div>
      <button
        class="icon-btn"
        title="Refresh"
        @click="refresh"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
        </svg>
      </button>
    </div>
    <div
      v-if="loading"
      class="file-loading"
    >
      Loading...
    </div>
    <div
      v-else-if="entries.length === 0"
      class="panel-empty"
    >
      Empty directory
    </div>
    <div
      v-else
      class="file-list"
    >
      <div
        v-for="entry in entries"
        :key="entry.path"
        :class="['file-item', { 'is-dir': entry.type === 'dir' }]"
        @click="handleClick(entry)"
        @dblclick="handleDblClick(entry)"
      >
        <span
          class="file-icon"
          v-html="entry.type === 'dir' ? folderIcon : getFileIcon(entry.name)"
        />
        <span class="file-name">{{ entry.name }}</span>
        <span
          v-if="entry.type === 'file'"
          class="file-size"
        >{{ formatSize(entry.size) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useApi } from '@/composables/useApi.js'

const emit = defineEmits(['open-file'])
const api = useApi()

const currentPath = ref('')
const entries = ref([])
const loading = ref(false)

const folderIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>'
const fileIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
const codeIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'

const codeExts = new Set(['py', 'js', 'ts', 'vue', 'jsx', 'tsx', 'json', 'yaml', 'yml', 'toml', 'css', 'scss', 'html', 'xml', 'md', 'sh', 'bash', 'rs', 'go', 'java', 'c', 'cpp', 'h', 'rb', 'php', 'sql', 'lua', 'zig', 'svelte'])

function getFileIcon(name) {
  const ext = name.split('.').pop().toLowerCase()
  return codeExts.has(ext) ? codeIcon : fileIcon
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function loadDir(path) {
  loading.value = true
  try {
    const data = await api.listFiles(path)
    entries.value = data.entries || []
    currentPath.value = path || ''
  } catch {
    entries.value = []
  } finally {
    loading.value = false
  }
}

function handleClick(entry) {
  if (entry.type === 'dir') {
    loadDir(entry.path)
  }
}

function handleDblClick(entry) {
  if (entry.type === 'file') {
    emit('open-file', { path: entry.path, name: entry.name })
  }
}

function goUp() {
  if (!currentPath.value) return
  const parts = currentPath.value.split('/')
  parts.pop()
  loadDir(parts.join('/'))
}

function refresh() {
  loadDir(currentPath.value)
}

onMounted(() => loadDir(''))
</script>

<style scoped>
.file-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.file-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.path-display {
  flex: 1;
  font-size: 11px;
  color: var(--t3);
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0 4px;
}

.icon-btn {
  background: none;
  border: 1px solid transparent;
  color: var(--t3);
  cursor: pointer;
  border-radius: 5px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.12s;
  flex-shrink: 0;
}

.icon-btn:hover {
  background: var(--bg2);
  color: var(--t1);
  border-color: var(--border);
}

.icon-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

.icon-btn svg {
  width: 14px;
  height: 14px;
}

.file-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  font-family: var(--mono);
  color: var(--t1);
  transition: background 0.1s;
  user-select: none;
}

.file-item:hover {
  background: var(--bg2);
}

.file-item.is-dir {
  color: var(--acc2);
}

.file-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-icon :deep(svg) {
  width: 14px;
  height: 14px;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-size: 10px;
  color: var(--t3);
  flex-shrink: 0;
}

.file-loading,
.panel-empty {
  padding: 20px;
  text-align: center;
  color: var(--t3);
  font-size: 12px;
}
</style>
