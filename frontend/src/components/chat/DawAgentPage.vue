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

function scrollToBottom() {
  nextTick(() => {
    if (chatArea.value) {
      chatArea.value.scrollTop = chatArea.value.scrollHeight
    }
  })
}

function setWorkspace(nextWorkspace) {
  workspace.value = nextWorkspace === 'host_project' ? 'host_project' : 'atri_studio'
}

async function handleSend(payload) {
  const text = typeof payload === 'string' ? payload : payload?.text || ''
  const images = Array.isArray(payload?.images) ? payload.images : []
  if ((!text.trim() && !images.length) || sending.value) return

  clearThinking()
  clearToolCards()
  addMessage('user', text, false)
  sending.value = true
  scrollToBottom()

  try {
    const result = await api.sendDawAgentMessage({
      message: text,
      projectSessionId: projectSessionId.value,
      instanceId: instanceId.value,
      workspace: workspace.value,
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
      await addAssistantHttpResponse(result)
    }
  } catch (err) {
    addMessage('assistant', `Connection error: ${err.message}`, false)
  } finally {
    sending.value = false
    clearThinking()
    clearToolCards()
    scrollToBottom()
  }
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

onMounted(async () => {
  await loadProjectTranscript()
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
