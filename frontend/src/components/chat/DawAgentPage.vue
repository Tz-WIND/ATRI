<template>
  <div class="daw-agent-page">
    <header class="daw-agent-header">
      <span class="header-title">ATRI Bridge</span>
    </header>

    <div
      ref="chatArea"
      class="daw-agent-messages"
    >
      <div
        v-if="hostProjectSyncStatus"
        class="host-sync-status"
        role="status"
      >
        {{ hostProjectSyncStatus }}
      </div>
      <div
        v-if="workspace === 'host_project'"
        class="dawproject-snapshot-panel"
      >
        <div class="snapshot-main">
          <span class="snapshot-label">DAWproject snapshot</span>
          <span class="snapshot-value">{{ snapshotStatusLabel }}</span>
        </div>
        <label class="snapshot-toggle">
          <input
            v-model="autoImportOnSend"
            type="checkbox"
          >
          <span>Import snapshot on send</span>
        </label>
        <div class="snapshot-actions">
          <button
            type="button"
            class="snapshot-button"
            @click="loadDawprojectSnapshotStatus"
          >
            Refresh
          </button>
          <button
            type="button"
            class="snapshot-button"
            @click="copySnapshotFolderPath"
          >
            Copy folder path
          </button>
          <button
            type="button"
            class="snapshot-button"
            @click="requestStudioOneSnapshotExport"
          >
            Request Studio One export
          </button>
        </div>
      </div>
      <div
        v-if="messages.length === 0"
        class="empty-state"
      >
        <div class="empty-logo">
          ATRI
        </div>
        <div class="empty-subtitle">
          DAW agent workspace
        </div>
      </div>
      <template
        v-for="item in displayItems"
        :key="item.id"
      >
        <AgentTodoPanel
          v-if="item.type === 'todo'"
          :todo-snapshot="item.message.todoSnapshot"
        />
        <ToolCard
          v-else-if="item.type === 'tool'"
          :tool-data="item.message.toolData"
        />
        <ToolCard
          v-else-if="item.type === 'tool-group'"
          :tool-group="item.tools"
        />
        <ThinkingBlock
          v-else-if="item.type === 'thinking'"
          :thinking="item.message"
        />
        <ChatMessage
          v-else
          :message="item.message"
        />
      </template>
      <div
        v-if="showThinkingIndicator"
        class="thinking-indicator"
      >
        <span class="pulse-text">Thinking</span>
      </div>
    </div>

    <ChatInput
      :sending="sending"
      :agent-mode="agentMode"
      :mode-pending="modePending"
      :workspace="workspace"
      daw-workspace-picker
      @send="handleSend"
      @cancel="handleCancel"
      @set-mode="handleSetMode"
      @set-workspace="setWorkspace"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import AgentTodoPanel from './AgentTodoPanel.vue'
import ChatInput from './ChatInput.vue'
import ChatMessage from './ChatMessage.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCard from './ToolCard.vue'
import { buildChatDisplayItems } from '@/composables/chatDisplayItems.js'
import { useApi } from '@/composables/useApi.js'
import { useChat } from '@/composables/useChat.js'
import { useDawHost } from '@/composables/useDawHost.js'
import { useProviders } from '@/composables/useProviders.js'
import { useSession } from '@/composables/useSession.js'
import { useWebSocket } from '@/composables/useWebSocket.js'

const api = useApi()
const { activeModel, activeModelProvider, loadStatus } = useProviders()
const { handleProjectBroadcast } = useDawHost()
const {
  messages,
  sending,
  thinkingBlock,
  toolCards,
  handleWsEvent,
  addMessage,
  addAssistantHttpResponse,
  clearThinking,
  clearToolCards,
  loadTranscript,
} = useChat()
const { loadSessionMessages } = useSession()

const params = new URLSearchParams(window.location.search)
const projectSessionId = ref(params.get('project_session_id') || params.get('project') || 'default_project')
const instanceId = ref(params.get('instance_id') || params.get('instance') || '')
const workspace = ref(params.get('workspace') === 'host_project' ? 'host_project' : 'atri_studio')
const hostName = ref(params.get('host') || 'Studio One')
const agentMode = ref('agent')
const modePending = ref(false)
const chatArea = ref(null)
const AUTO_IMPORT_STORAGE_KEY = 'atri.daw-agent.host-project-auto-import'

function readAutoImportPreference() {
  try {
    const stored = localStorage.getItem(AUTO_IMPORT_STORAGE_KEY)
    if (stored === '0' || stored === 'false') return false
    if (stored === '1' || stored === 'true') return true
  } catch {}
  return true
}

const hostProjectSyncStatus = ref('')
const snapshotStatus = ref(null)
const snapshotStatusPending = ref(false)
const autoImportOnSend = ref(readAutoImportPreference())
let handledEventCount = 0
let processingEvents = false

const currentThreadId = computed(() => `daw_agent:friend:${projectSessionId.value || 'default_project'}`)
const { events } = useWebSocket(currentThreadId, { surface: 'daw-agent' })

const displayItems = computed(() => buildChatDisplayItems(messages.value))

const hasExecutingTool = computed(() =>
  Object.values(toolCards.value).some((tool) => tool.status === 'executing'),
)

const showThinkingIndicator = computed(() =>
  sending.value && !thinkingBlock.value && !hasExecutingTool.value,
)

const snapshotStatusLabel = computed(() => {
  const snapshot = snapshotStatus.value?.latest_snapshot
  if (snapshot?.filename) {
    return `${snapshot.filename}${snapshot.ready ? '' : ' (not ready)'}`
  }
  const request = snapshotStatus.value?.export_request
  if (request?.host) {
    return `Awaiting ${request.host} export`
  }
  return 'Export DAWproject to this folder before sending'
})

function scrollToBottom() {
  nextTick(() => {
    if (chatArea.value) {
      chatArea.value.scrollTop = chatArea.value.scrollHeight
    }
  })
}

function setWorkspace(nextWorkspace) {
  workspace.value = nextWorkspace === 'host_project' ? 'host_project' : 'atri_studio'
  if (workspace.value !== 'host_project') {
    hostProjectSyncStatus.value = ''
  } else {
    loadDawprojectSnapshotStatus()
  }
}

async function handleSend(payload) {
  const text = typeof payload === 'string' ? payload : payload?.text || ''
  const images = Array.isArray(payload?.images) ? payload.images : []
  if ((!text.trim() && !images.length) || sending.value) return

  clearThinking()
  clearToolCards()
  addMessage('user', text, false)
  sending.value = true
  const hostAutoImport = workspace.value === 'host_project' && autoImportOnSend.value
  hostProjectSyncStatus.value = hostAutoImport
    ? 'Importing latest DAWproject snapshot...'
    : ''
  scrollToBottom()

  try {
    const result = await api.sendDawAgentMessage({
      message: text,
      projectSessionId: projectSessionId.value,
      instanceId: instanceId.value,
      workspace: workspace.value,
      syncHostProject: hostAutoImport,
      requestHostExport: false,
      hostContext: {
        host: hostName.value,
        workspace: workspace.value,
      },
      images,
      model: activeModel.value,
      modelProvider: activeModelProvider.value,
    })
    if (result.error) {
      addMessage('assistant', `Error: ${result.error}`, false)
    } else {
      hostProjectSyncStatus.value = formatHostProjectSyncStatus(result.host_project_sync)
      await loadDawprojectSnapshotStatus()
      await addAssistantHttpResponse(result)
    }
  } catch (err) {
    if (workspace.value === 'host_project') {
      hostProjectSyncStatus.value = 'DAWproject snapshot import not completed'
    }
    addMessage('assistant', `Connection error: ${err.message}`, false)
  } finally {
    sending.value = false
    clearThinking()
    clearToolCards()
    scrollToBottom()
  }
}

async function loadDawprojectSnapshotStatus() {
  if (snapshotStatusPending.value) return
  snapshotStatusPending.value = true
  try {
    snapshotStatus.value = await api.studioDawprojectSnapshotStatus()
  } finally {
    snapshotStatusPending.value = false
  }
}

async function requestStudioOneSnapshotExport() {
  const result = await api.studioDawprojectSnapshotRequestExport({
    host: 'Studio One',
    source: 'daw_agent',
    instance_id: instanceId.value,
  })
  snapshotStatus.value = {
    ...(snapshotStatus.value || {}),
    export_request: result.request,
  }
  hostProjectSyncStatus.value = 'Studio One DAWproject export requested'
}

async function copySnapshotFolderPath() {
  const path = snapshotStatus.value?.inbox_path || ''
  if (!path) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(path)
    } else {
      throw new Error('clipboard unavailable')
    }
    hostProjectSyncStatus.value = 'DAWproject snapshot folder path copied'
  } catch {
    hostProjectSyncStatus.value = `Snapshot folder: ${path}`
  }
}

function formatHostProjectSyncStatus(sync) {
  if (!sync || typeof sync !== 'object') return ''
  const filename = sync.filename || 'latest export'
  const notes = Number(sync.note_count || 0)
  if (sync.status === 'imported') {
    return `Imported DAWproject snapshot: ${filename} (${notes} notes)`
  }
  if (sync.status === 'unchanged') {
    return `DAWproject snapshot already imported: ${filename} (${notes} notes)`
  }
  if (sync.status === 'missing') {
    return 'No DAWproject snapshot found'
  }
  if (sync.status === 'error') {
    return `DAWproject snapshot import failed: ${sync.error || filename}`
  }
  return ''
}

async function handleCancel() {
  if (!sending.value) return
  await api.cancelChat(currentThreadId.value).catch(() => null)
}

async function handleSetMode(mode) {
  const nextMode = mode === 'plan' ? 'plan' : 'agent'
  if (agentMode.value === nextMode || modePending.value) return
  modePending.value = true
  try {
    const data = await api.setAgentMode(nextMode, 'daw agent mode switch')
    agentMode.value = data.mode === 'plan' ? 'plan' : 'agent'
  } finally {
    modePending.value = false
  }
}

async function processEvents() {
  if (processingEvents) return
  processingEvents = true
  try {
    while (handledEventCount < events.value.length) {
      const event = events.value[handledEventCount]
      handledEventCount += 1
      if (event.type === 'mode_changed') {
        agentMode.value = event.mode === 'plan' ? 'plan' : 'agent'
      } else if (event.type === 'music_project') {
        await handleProjectBroadcast(event)
      } else {
        handleWsEvent(event)
      }
      scrollToBottom()
      await nextTick()
    }
  } finally {
    processingEvents = false
  }
}

async function loadProjectTranscript() {
  handledEventCount = events.value.length
  const transcript = await loadSessionMessages(currentThreadId.value)
  loadTranscript(transcript)
  scrollToBottom()
}

watch(events, () => {
  processEvents()
}, { deep: false })

watch(messages, () => scrollToBottom(), { deep: true })

watch(autoImportOnSend, (enabled) => {
  try {
    localStorage.setItem(AUTO_IMPORT_STORAGE_KEY, enabled ? '1' : '0')
  } catch {}
})

onMounted(async () => {
  await loadProjectTranscript()
  if (workspace.value === 'host_project') {
    await loadDawprojectSnapshotStatus().catch(() => null)
  }
  await loadStatus().catch(() => null)
  try {
    const data = await api.getAgentMode()
    agentMode.value = data.mode === 'plan' ? 'plan' : 'agent'
  } catch {
    agentMode.value = 'agent'
  }
})
</script>

<style scoped>
.daw-agent-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-width: 0;
  background: var(--app-bg);
  color: var(--t1);
}

.daw-agent-header {
  height: 34px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
  background: rgba(24, 24, 24, 0.72);
}

.header-title {
  flex-shrink: 0;
  color: var(--t1);
  font-size: 13px;
  font-weight: 700;
}

.daw-agent-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 18px 10px;
}

.host-sync-status {
  width: fit-content;
  max-width: min(900px, 100%);
  margin: 0 auto 12px;
  padding: 5px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.dawproject-snapshot-panel {
  max-width: 900px;
  margin: 0 auto 12px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.035);
}

.snapshot-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.snapshot-label {
  flex-shrink: 0;
  color: var(--t2);
  font-size: 12px;
  font-weight: 700;
}

.snapshot-value {
  min-width: 0;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
  text-align: right;
  overflow-wrap: anywhere;
}

.snapshot-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  color: var(--t3);
  font-size: 11px;
  cursor: pointer;
  user-select: none;
}

.snapshot-toggle input {
  margin: 0;
  accent-color: var(--acc2);
}

.snapshot-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.snapshot-button {
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--t2);
  font-size: 11px;
}

.snapshot-button:hover {
  color: var(--t1);
  background: rgba(255, 255, 255, 0.08);
}

.empty-state {
  display: grid;
  gap: 6px;
  justify-items: center;
  padding: 70px 12px;
  color: var(--t3);
}

.empty-logo {
  color: var(--t1);
  font-family: var(--mono);
  font-size: 28px;
  font-weight: 700;
}

.empty-subtitle {
  font-size: 13px;
}

.thinking-indicator {
  max-width: 900px;
  margin: 8px auto 10px;
  padding-left: 34px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 12px;
}

.pulse-text {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--t3);
  animation: pulse 1.5s ease-in-out infinite;
}

.pulse-text::before {
  content: "";
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--acc2);
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
</style>
