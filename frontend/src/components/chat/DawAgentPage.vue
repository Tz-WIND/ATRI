<template>
  <div class="daw-agent-page">
    <header class="daw-agent-header">
      <span class="header-title">ATRI Bridge</span>
      <span
        :class="['header-status-dot', wsConnected ? 'on' : 'off']"
        :title="wsConnected ? 'Connected' : 'Disconnected'"
      />
    </header>

    <ConnectionBanner
      :opened-once="wsOpenedOnce"
      :connected="wsConnected"
      :reconnect-delay-ms="wsReconnectDelay"
      @reconnect="handleReconnect"
    />

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
          :retry-disabled="sending"
          @retry="handleRetryMessage"
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AgentTodoPanel from './AgentTodoPanel.vue'
import ChatInput from './ChatInput.vue'
import ChatMessage from './ChatMessage.vue'
import ConnectionBanner from './ConnectionBanner.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCard from './ToolCard.vue'
import {
  buildUserMessageAttachments,
  normalizeFilePayload,
  normalizeImagePayload,
} from '@/composables/chatAttachments.js'
import { useChatDisplayItems } from '@/composables/chatDisplayItems.js'
import { createChatRetryState } from '@/composables/chatRetryState.js'
import { useApi } from '@/composables/useApi.js'
import { useChat } from '@/composables/useChat.js'
import { useDawHost } from '@/composables/useDawHost.js'
import { useProviders } from '@/composables/useProviders.js'
import { useSession } from '@/composables/useSession.js'
import { useWebSocket } from '@/composables/useWebSocket.js'
import { createChatEventProcessor } from './chatEventProcessor.js'

const api = useApi()
const { activeModel, activeModelProvider, loadStatus } = useProviders()
const { handleProjectBroadcast } = useDawHost()
const {
  messages,
  sending,
  thinkingBlock,
  toolCards,
  handleWsEvent,
  beginTranscriptTurn,
  addMessage,
  addErrorMessage,
  dismissErrorMessages,
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
let scrollPending = false

const currentThreadId = computed(() => `daw_agent:friend:${projectSessionId.value || 'default_project'}`)
const { connected: wsConnected, openedOnce: wsOpenedOnce, events, reconnectDelayMs: wsReconnectDelay, reconnectNow } = useWebSocket(currentThreadId, { surface: 'daw-agent' })

const displayItems = useChatDisplayItems(messages)
const dawRetryState = createChatRetryState({
  getMessages: () => messages.value,
  isSending: () => sending.value,
  clearErrors: dismissErrorMessages,
  addUserMessage: (dawPayload) => addMessage('user', dawPayload.message, false, {
    attachments: buildUserMessageAttachments(dawPayload.images, dawPayload.files),
  }),
})

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
  if (scrollPending) return
  scrollPending = true
  nextTick(() => {
    scrollPending = false
    if (chatArea.value) {
      chatArea.value.scrollTop = chatArea.value.scrollHeight
    }
  })
}

async function handleDawWsEvent(event) {
  if (event.type === 'music_project') {
    await handleProjectBroadcast(event)
    return
  }
  handleWsEvent(event)
}

const eventProcessor = createChatEventProcessor({
  events,
  handleEvent: handleDawWsEvent,
  handleModeChanged: (mode) => {
    agentMode.value = mode === 'plan' ? 'plan' : 'agent'
  },
  scrollToBottom,
})

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
  const files = Array.isArray(payload?.files) ? payload.files : []
  const imagePayload = normalizeImagePayload(images)
  const filePayload = normalizeFilePayload(files)
  if ((!text.trim() && !imagePayload.length && !filePayload.length) || sending.value) return

  const dawPayload = buildDawSendPayload(text, imagePayload, filePayload)
  dawRetryState.beginFreshSend(dawPayload)
  await performDawSend(dawPayload, { isRetry: false })
}

// Builds the request body for sendDawAgentMessage. Shared by fresh sends and
// retries so the two paths can't drift.
function buildDawSendPayload(text, imagePayload, filePayload) {
  const hostAutoImport = workspace.value === 'host_project' && autoImportOnSend.value
  return {
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
    images: imagePayload,
    files: filePayload,
    model: activeModel.value,
    modelProvider: activeModelProvider.value,
  }
}

async function performDawSend(dawPayload, { isRetry = false } = {}) {
  clearThinking()
  clearToolCards()
  beginTranscriptTurn()
  sending.value = true
  const hostAutoImport = dawPayload.syncHostProject
  if (hostAutoImport) {
    hostProjectSyncStatus.value = 'Importing latest DAWproject snapshot...'
  } else if (!isRetry) {
    hostProjectSyncStatus.value = ''
  }
  scrollToBottom()

  try {
    const result = await api.sendDawAgentMessage(dawPayload)
    if (result.error) {
      addErrorMessage({
        title: 'Request failed',
        detail: String(result.error || ''),
        retriable: true,
        kind: 'request',
      })
    } else {
      hostProjectSyncStatus.value = formatHostProjectSyncStatus(result.host_project_sync)
      await loadDawprojectSnapshotStatus()
      await addAssistantHttpResponse(result)
    }
  } catch (err) {
    if (workspace.value === 'host_project') {
      hostProjectSyncStatus.value = 'DAWproject snapshot import not completed'
    }
    addErrorMessage({
      title: 'Connection error',
      detail: err?.message ? String(err.message) : 'Could not reach the server.',
      retriable: true,
      kind: 'connection',
    })
  } finally {
    sending.value = false
    clearThinking()
    clearToolCards()
    scrollToBottom()
  }
}

async function handleRetryMessage() {
  const dawPayload = dawRetryState.beginRetry()
  if (!dawPayload) return
  await performDawSend(dawPayload, { isRetry: true })
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

// Manual reconnect from the connection banner — bypasses backoff for an
// immediate retry.
function handleReconnect() {
  reconnectNow()
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

async function loadProjectTranscript() {
  dawRetryState.reset()
  eventProcessor.resetToEnd()
  const transcript = await loadSessionMessages(currentThreadId.value)
  loadTranscript(transcript)
  scrollToBottom()
}

watch(events, () => {
  eventProcessor.schedule()
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

onUnmounted(() => {
  dawRetryState.reset()
  eventProcessor.cancel()
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
  gap: 8px;
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

.header-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.header-status-dot.on {
  background: var(--ok);
  box-shadow: 0 0 8px rgba(143, 216, 199, 0.32);
}

.header-status-dot.off {
  background: var(--red);
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
