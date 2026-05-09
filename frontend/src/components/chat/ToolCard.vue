<template>
  <div :class="['tool-card', statusClass, { 'tool-card-group': isGroup }]">
    <button
      class="tool-trigger"
      type="button"
      :disabled="!hasDetails"
      :aria-expanded="hasDetails ? String(open) : undefined"
      @click="toggleOpen"
    >
      <svg
        v-if="hasDetails"
        :class="['tool-chevron', { open }]"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <polyline points="9 18 15 12 9 6" />
      </svg>
      <span
        v-else
        class="tool-chevron-spacer"
        aria-hidden="true"
      />

      <span
        class="tool-status-mark"
        aria-hidden="true"
      >
        <span
          v-if="isRunning"
          class="tool-spinner"
        />
        <span
          v-else-if="isFailed"
          class="tool-failed-mark"
        >!</span>
        <span
          v-else
          class="tool-neutral-mark"
        />
      </span>

      <span class="tool-info">
        <span class="tool-title">{{ title }}</span>
        <span
          v-if="subtitle && !isRunning"
          class="tool-subtitle"
        >{{ subtitle }}</span>
        <span
          v-for="arg in triggerArgs"
          :key="arg"
          class="tool-arg-pill"
        >{{ arg }}</span>
      </span>

      <span
        v-if="statusBadge"
        class="tool-badge"
      >{{ statusBadge }}</span>
    </button>

    <div
      v-if="open && hasDetails"
      class="tool-details"
    >
      <div
        v-if="isGroup"
        class="context-tool-list"
      >
        <div
          v-for="item in groupTools"
          :key="item.id || `${item.tool}-${item.status}-${toolLineSubtitle(item)}`"
          class="context-tool-row"
        >
          <span
            class="context-row-mark"
            :data-status="item.status || 'success'"
            aria-hidden="true"
          />
          <span class="context-row-title">{{ toolLineTitle(item) }}</span>
          <span
            v-if="toolLineSubtitle(item)"
            class="context-row-subtitle"
          >{{ toolLineSubtitle(item) }}</span>
        </div>
      </div>

      <template v-else>
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
          :raw="activeTool.result"
          :file-name="targetLabel"
        />

        <div
          v-if="resultCompressed"
          class="tool-compressed-note"
        >
          <span>Full output stored</span>
          <code v-if="resultId">{{ resultId }}</code>
        </div>

        <pre
          v-if="!diffContent && detailsText && !resultCompressed"
          class="tool-output"
        >{{ detailsText }}</pre>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import DiffViewer from './DiffViewer.vue'

const TOOL_LABELS = {
  read_file: { title: 'Read', ing: 'Reading', done: 'Read', fail: 'Read failed', key: 'file_path' },
  write_file: { title: 'Write', ing: 'Writing', done: 'Wrote', fail: 'Write failed', key: 'file_path' },
  edit_file: { title: 'Edit', ing: 'Editing', done: 'Edited', fail: 'Edit failed', key: 'file_path' },
  find_replace: { title: 'Replace', ing: 'Replacing', done: 'Replaced', fail: 'Replace failed', key: 'file_path' },
  apply_patch: { title: 'Patch', ing: 'Patching', done: 'Patched', fail: 'Patch failed', key: 'file_path' },
  glob: { title: 'Glob', ing: 'Searching files', done: 'Searched files', fail: 'Search failed', key: 'pattern' },
  grep: { title: 'Grep', ing: 'Searching', done: 'Searched', fail: 'Search failed', key: 'pattern' },
  search: { title: 'Search', ing: 'Searching', done: 'Searched', fail: 'Search failed', key: 'query' },
  retrieve_tool_result: { title: 'Retrieve output', ing: 'Retrieving output', done: 'Retrieved output', fail: 'Retrieve failed', key: 'result_id' },
  list_dir: { title: 'List', ing: 'Listing', done: 'Listed', fail: 'List failed', key: 'path' },
  tree: { title: 'Tree', ing: 'Viewing tree', done: 'Viewed tree', fail: 'Tree failed', key: 'path' },
  bash: { title: 'Shell', ing: 'Running shell', done: 'Ran shell', fail: 'Shell failed', key: 'command' },
  terminal: { title: 'Terminal', ing: 'Running terminal', done: 'Ran terminal', fail: 'Terminal failed', key: 'command' },
  agent: { title: 'Agent', ing: 'Running agent', done: 'Agent done', fail: 'Agent failed', key: 'task' },
}

const props = defineProps({
  toolData: { type: Object, default: null },
  toolGroup: { type: Array, default: null },
})

const open = ref(false)

const groupTools = computed(() => (Array.isArray(props.toolGroup) ? props.toolGroup : []))
const isGroup = computed(() => groupTools.value.length > 0)
const activeTool = computed(() => props.toolData || {})
const args = computed(() => activeTool.value.args || {})

const status = computed(() => {
  if (!isGroup.value) return activeTool.value.status || 'success'
  if (groupTools.value.some((tool) => tool.status === 'executing')) return 'executing'
  if (groupTools.value.some((tool) => tool.status === 'failed')) return 'failed'
  return 'success'
})

const statusClass = computed(() => status.value)
const isRunning = computed(() => status.value === 'executing')
const isFailed = computed(() => status.value === 'failed')

watch(status, (next) => {
  if (next === 'executing') open.value = false
  if (next === 'failed') open.value = true
})

const phase = computed(() => {
  if (isRunning.value) return 'ing'
  if (isFailed.value) return 'fail'
  return 'done'
})

const title = computed(() => {
  if (isGroup.value) {
    if (isRunning.value) return 'Gathering context'
    if (isFailed.value) return 'Context failed'
    return 'Gathered context'
  }

  const cfg = toolConfig(activeTool.value.tool)
  return cfg[phase.value] || cfg.title
})

const subtitle = computed(() => {
  if (isGroup.value) return groupSummary.value

  const tool = activeTool.value
  const cfg = toolConfig(tool.tool)
  const raw = tool.tool === 'bash' || tool.tool === 'terminal'
    ? args.value.description || args.value.command
    : args.value[cfg.key]
  return compactPath(String(raw || ''), 74)
})

const groupSummary = computed(() => {
  const read = groupTools.value.filter((tool) => tool.tool === 'read_file').length
  const listed = groupTools.value.filter((tool) => tool.tool === 'list_dir' || tool.tool === 'tree').length
  const search = groupTools.value.filter((tool) => ['glob', 'grep', 'search'].includes(tool.tool)).length
  return [
    countLabel(read, 'read'),
    countLabel(search, 'search'),
    countLabel(listed, 'list'),
  ].filter(Boolean).join(' / ')
})

const triggerArgs = computed(() => {
  if (isGroup.value || isRunning.value) return []
  if (readRange.value) return [readRange.value]

  const targetKey = toolConfig(activeTool.value.tool).key
  return Object.entries(args.value || {})
    .filter(([key]) => key !== targetKey && !detailHiddenKeys.has(key))
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 2)
    .map(([key, value]) => `${key}=${previewValue(value, 42)}`)
})

const hasDetails = computed(() => {
  if (isGroup.value) return groupTools.value.length > 0
  if (isRunning.value) return false
  return Boolean(argsEntries.value.length || diffContent.value || detailsText.value || resultCompressed.value)
})

const statusBadge = computed(() => {
  if (resultCompressed.value) return 'stored'
  if (isRunning.value) return 'running'
  if (isFailed.value) return 'failed'
  return ''
})

const detailsText = computed(() => {
  if (isRunning.value || isGroup.value) return ''
  const tool = activeTool.value
  const output = String(tool.result || '')
  if (tool.tool === 'bash' || tool.tool === 'terminal') {
    const command = args.value.command || tool.metadata?.command || ''
    return command ? `$ ${command}${output ? `\n\n${output}` : ''}` : output
  }
  return output
})

const resultCompressed = computed(() =>
  Boolean(activeTool.value.resultCompressed) ||
  String(activeTool.value.result || '').startsWith('<persisted-output>')
)

const resultId = computed(() => {
  if (activeTool.value.resultId) return activeTool.value.resultId
  const match = String(activeTool.value.result || '').match(/^(?:tool_result_id|Tool result id):\s*(\S+)/m)
  return match ? match[1] : ''
})

const readRange = computed(() => {
  if (activeTool.value.tool !== 'read_file') return ''
  const offset = Number(args.value.offset || 1)
  const limit = Number(args.value.limit || 0)
  if (!limit) return ''
  return `L${offset}-L${offset + limit - 1}`
})

const targetLabel = computed(() => {
  const key = toolConfig(activeTool.value.tool).key
  const target = key ? args.value[key] : ''
  return target ? compactPath(String(target), 80) : ''
})

const detailHiddenKeys = new Set(['content', 'old_string', 'new_string'])

const argsEntries = computed(() => {
  const targetKey = toolConfig(activeTool.value.tool).key
  return Object.entries(args.value || {})
    .filter(([key]) => !detailHiddenKeys.has(key))
    .filter(([key]) => !(activeTool.value.tool === 'bash' && key === 'command'))
    .filter(([key]) => !(activeTool.value.tool === 'terminal' && key === 'command'))
    .filter(([key]) => key !== targetKey || !subtitle.value)
    .map(([key, value]) => ({
      key,
      value: previewValue(value, 180),
    }))
})

const diffContent = computed(() => {
  if (isRunning.value || isGroup.value) return ''
  return extractDiff(activeTool.value.result || '')
})

function toggleOpen() {
  if (!hasDetails.value) return
  open.value = !open.value
}

function toolLineTitle(tool) {
  const cfg = toolConfig(tool.tool)
  if (tool.status === 'executing') return cfg.ing || cfg.title
  if (tool.status === 'failed') return cfg.fail || `${cfg.title} failed`
  return cfg.done || cfg.title
}

function toolLineSubtitle(tool) {
  const toolArgs = tool.args || {}
  const cfg = toolConfig(tool.tool)
  const raw = toolArgs[cfg.key]
  const range = tool.tool === 'read_file' ? lineRange(toolArgs) : ''
  return [compactPath(String(raw || ''), 74), range].filter(Boolean).join(' ')
}

function lineRange(toolArgs) {
  const offset = Number(toolArgs.offset || 1)
  const limit = Number(toolArgs.limit || 0)
  if (!limit) return ''
  return `L${offset}-L${offset + limit - 1}`
}

function toolConfig(name) {
  return TOOL_LABELS[name] || { title: normalizeToolName(name || 'tool'), key: 'name' }
}

function countLabel(count, label) {
  if (!count) return ''
  return `${count} ${label}${count === 1 ? '' : 's'}`
}

function extractDiff(text) {
  if (!text) return ''
  const lines = String(text).split(/\r?\n/)
  const start = lines.findIndex((line, index) =>
    line.startsWith('diff ') ||
    (line.startsWith('--- ') && lines[index + 1]?.startsWith('+++ '))
  )
  return start >= 0 ? lines.slice(start).join('\n') : ''
}

function compactPath(value, max = 58) {
  if (!value) return ''
  return value.length > max ? '...' + value.slice(-(max - 3)) : value
}

function previewValue(value, max = 120) {
  let text = ''
  if (typeof value === 'string') {
    text = value
  } else {
    try {
      text = JSON.stringify(value)
    } catch {
      text = String(value)
    }
  }
  if (!text) return ''
  return text.length > max ? text.slice(0, max) + '...' : text
}

function normalizeToolName(value) {
  return String(value || 'tool')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}
</script>

<style scoped>
.tool-card {
  width: 100%;
  max-width: 900px;
  margin: 8px auto 14px;
  padding: 3px 0 5px;
  color: var(--t3);
  font-family: var(--sans);
  font-size: 13px;
}

.tool-card.executing {
  color: var(--t2);
}

.tool-card.failed {
  color: var(--red);
}

.tool-trigger {
  width: auto;
  max-width: 100%;
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.tool-trigger:disabled {
  cursor: default;
}

.tool-trigger:not(:disabled):hover .tool-title,
.tool-trigger:not(:disabled):hover .tool-subtitle {
  color: var(--t1);
}

.tool-chevron,
.tool-chevron-spacer {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.tool-chevron {
  color: var(--t3);
  transition: transform 0.15s ease;
}

.tool-chevron.open {
  transform: rotate(90deg);
}

.tool-status-mark {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.tool-spinner {
  width: 13px;
  height: 13px;
  border: 1px solid rgba(55, 148, 255, 0.22);
  border-top-color: var(--acc2);
  border-radius: 50%;
  animation: tool-spin 0.9s linear infinite;
}

.tool-failed-mark {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--red);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
}

.tool-neutral-mark {
  width: 10px;
  height: 10px;
}

.tool-info {
  min-width: 0;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  overflow: hidden;
}

.tool-title {
  flex: 0 0 auto;
  color: var(--t1);
  font-weight: 600;
  line-height: 1.7;
  transition: color 0.12s ease;
}

.executing .tool-title {
  background: linear-gradient(90deg, var(--t2), var(--acc2), var(--t2));
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: tool-shimmer 1.4s ease-in-out infinite;
}

.tool-subtitle,
.tool-arg-pill {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t2);
  line-height: 1.7;
  transition: color 0.12s ease;
}

.tool-subtitle {
  max-width: min(560px, 62vw);
}

.tool-arg-pill {
  max-width: 220px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 12px;
}

.tool-badge {
  flex: 0 0 auto;
  color: currentColor;
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1;
}

.tool-details {
  margin-top: 4px;
  padding: 4px 0 0 35px;
  color: var(--t3);
}

.context-tool-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-left: 2px;
}

.context-tool-row {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
}

.context-row-mark {
  width: 6px;
  height: 6px;
  flex: 0 0 6px;
  border-radius: 50%;
  background: var(--ok);
  transform: translateY(-1px);
}

.context-row-mark[data-status="executing"] {
  background: var(--acc2);
  animation: context-pulse 1.2s ease-in-out infinite;
}

.context-row-mark[data-status="failed"] {
  background: var(--red);
}

.context-row-title {
  flex: 0 0 auto;
  color: var(--t2);
  font-weight: 500;
}

.context-row-subtitle {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t3);
}

.tool-args {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
}

.tool-arg {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 12px;
}

.arg-key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-arg code,
.tool-output {
  margin: 0;
  padding: 0 0 0 10px;
  background: transparent;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 0;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--t2);
}

.tool-arg code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-compressed-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding-left: 10px;
  border-left: 1px solid rgba(55, 148, 255, 0.24);
  color: var(--t2);
  font-family: var(--mono);
  font-size: 12px;
}

.tool-compressed-note code {
  color: var(--acc2);
}

.tool-output {
  width: 100%;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@keyframes tool-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes tool-shimmer {
  0% {
    background-position: 180% 0;
  }
  100% {
    background-position: -40% 0;
  }
}

@keyframes context-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}
</style>
