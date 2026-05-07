<template>
  <div :class="['tool-card', statusClass]">
    <button
      class="tool-header"
      type="button"
      @click="open = !open"
    >
      <svg
        :class="['tool-chevron', { open }]"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <polyline points="9 18 15 12 9 6" />
      </svg>
      <span
        class="tool-status-icon"
        v-html="statusIcon"
      />
      <span class="tool-label">{{ label }}</span>
      <span
        v-if="statusText"
        class="tool-pill"
      >{{ statusText }}</span>
    </button>
    <div
      v-if="open"
      class="tool-details"
    >
      <div
        v-if="argsEntries.length"
        class="tool-args"
      >
        <div
          v-for="entry in argsEntries"
          :key="entry.key"
          class="tool-arg"
        >
          <span class="arg-key">{{ entry.key }}</span>
          <code>{{ entry.value }}</code>
        </div>
      </div>
      <DiffViewer
        v-if="diffContent"
        :diff="diffContent"
        :raw="toolData.result"
        :file-name="targetLabel"
      />
      <pre
        v-else-if="detailsText"
        class="tool-output"
      >{{ detailsText }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import DiffViewer from './DiffViewer.vue'

const TOOL_LABELS = {
  read_file: { ing: 'Reading', done: 'Read', fail: 'Read Failed', key: 'file_path' },
  write_file: { ing: 'Writing', done: 'Wrote', fail: 'Write Failed', key: 'file_path' },
  edit_file: { ing: 'Editing', done: 'Edited', fail: 'Edit Failed', key: 'file_path' },
  find_replace: { ing: 'Replacing in', done: 'Replaced in', fail: 'Replace Failed', key: 'file_path' },
  glob: { ing: 'Searching files', done: 'Found files', fail: 'Search Failed', key: 'pattern' },
  grep: { ing: 'Searching', done: 'Searched', fail: 'Search Failed', key: 'pattern' },
  search: { ing: 'Searching', done: 'Searched', fail: 'Search Failed', key: 'query' },
  list_dir: { ing: 'Listing', done: 'Listed', fail: 'List Failed', key: 'path' },
  tree: { ing: 'Viewing tree', done: 'Viewed tree', fail: 'Tree Failed', key: 'path' },
  bash: { ing: 'Running command', done: 'Ran command', fail: 'Command Failed', key: 'command' },
  terminal: { ing: 'Running terminal', done: 'Ran terminal', fail: 'Terminal Failed', key: 'command' },
  agent: { ing: 'Running sub-agent', done: 'Sub-agent done', fail: 'Sub-agent Failed', key: 'task' },
}

const props = defineProps({
  toolData: { type: Object, required: true },
})

const open = ref(props.toolData.status === 'executing')

const statusClass = computed(() => props.toolData.status)

const statusIcon = computed(() => {
  switch (props.toolData.status) {
    case 'executing': return '&#10227;'
    case 'success': return '&#10003;'
    case 'failed': return '&#10007;'
    default: return ''
  }
})

watch(() => props.toolData.status, (status) => {
  if (status === 'executing') open.value = true
})

const phase = computed(() => {
  switch (props.toolData.status) {
    case 'executing': return 'ing'
    case 'success': return 'done'
    case 'failed': return 'fail'
    default: return 'done'
  }
})

const label = computed(() => {
  const cfg = TOOL_LABELS[props.toolData.tool]
  if (!cfg) {
    const verb = props.toolData.status === 'executing' ? props.toolData.tool + '...' : props.toolData.tool
    return verb
  }
  const target = props.toolData.args?.[cfg.key] ? String(props.toolData.args[cfg.key]) : ''
  const short = compactPath(target)
  const verb = cfg[phase.value] || props.toolData.tool
  const range = readRange.value ? ' ' + readRange.value : ''
  return short ? verb + ' ' + short + range : verb
})

const detailsText = computed(() => {
  if (!props.toolData.args && !props.toolData.result) return ''
  if (props.toolData.status === 'executing') return ''
  return props.toolData.result || ''
})

const statusText = computed(() => {
  switch (props.toolData.status) {
    case 'executing': return 'running'
    case 'failed': return 'failed'
    default: return ''
  }
})

const readRange = computed(() => {
  if (props.toolData.tool !== 'read_file') return ''
  const offset = Number(props.toolData.args?.offset || 1)
  const limit = Number(props.toolData.args?.limit || 0)
  if (!limit) return ''
  return `L${offset}-L${offset + limit - 1}`
})

const targetLabel = computed(() => {
  const cfg = TOOL_LABELS[props.toolData.tool]
  const key = cfg?.key
  const target = key ? props.toolData.args?.[key] : ''
  return target ? compactPath(String(target), 80) : ''
})

const argsEntries = computed(() => {
  const args = props.toolData.args || {}
  const hidden = new Set(['content', 'old_string', 'new_string'])
  return Object.entries(args)
    .filter(([key]) => !hidden.has(key))
    .map(([key, value]) => ({
      key,
      value: previewValue(value, 180),
    }))
})

function extractDiff(text) {
  if (!text) return ''
  const lines = text.split(/\r?\n/)
  const start = lines.findIndex((line, i) =>
    line.startsWith('diff ') ||
    (line.startsWith('--- ') && lines[i + 1]?.startsWith('+++ '))
  )
  return start >= 0 ? lines.slice(start).join('\n') : ''
}

const diffContent = computed(() => {
  if (props.toolData.status === 'executing') return ''
  return extractDiff(props.toolData.result || '')
})

function compactPath(value, max = 58) {
  if (!value) return ''
  return value.length > max ? '...' + value.slice(-(max - 3)) : value
}

function previewValue(value, max = 120) {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  if (!text) return ''
  return text.length > max ? text.slice(0, max) + '...' : text
}
</script>

<style scoped>
.tool-card {
  margin: 8px auto;
  max-width: 900px;
  background: rgba(37, 37, 38, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 9px;
  font-family: var(--mono);
  font-size: 12px;
  overflow: hidden;
  transition: border-color 0.2s, background 0.2s;
}

.tool-card.executing { border-color: rgba(55, 148, 255, 0.42); }
.tool-card.success { border-color: rgba(255, 255, 255, 0.1); }
.tool-card.failed { border-color: rgba(244, 135, 113, 0.38); }

.tool-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: var(--t2);
  transition: background 0.12s;
  user-select: none;
  text-align: left;
  font-family: inherit;
  font-size: inherit;
}

.tool-header:hover { background: rgba(255, 255, 255, 0.035); }

.tool-chevron {
  width: 13px;
  height: 13px;
  color: var(--t3);
  transition: transform 0.15s;
  flex-shrink: 0;
}

.tool-chevron.open { transform: rotate(90deg); }

.tool-status-icon {
  flex-shrink: 0;
  font-size: 13px;
  line-height: 1;
  width: 16px;
  text-align: center;
}

.executing .tool-status-icon {
  color: var(--acc2);
  animation: spin 1s linear infinite;
}

.success .tool-status-icon { color: var(--green); }
.failed .tool-status-icon { color: var(--red); }

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tool-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-pill {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--t3);
  background: rgba(255, 255, 255, 0.06);
  font-size: 10px;
}

.tool-details {
  padding: 0 12px 12px 39px;
}

.tool-args {
  display: grid;
  gap: 6px;
  margin-bottom: 8px;
}

.tool-arg {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  color: var(--t3);
}

.arg-key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-arg code,
.tool-output {
  margin: 0;
  padding: 8px;
  background: var(--bg0);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--t2);
  font-family: var(--mono);
}

.tool-arg code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-output {
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 260px;
  overflow: auto;
}
</style>
