<template>
  <div class="chat-page">
    <PageHeader title="Chat">
      <template #status>
        <span :class="['status-dot', wsConnected ? 'on' : 'off']" />
        <span
          v-if="activeModel"
          class="header-model"
        >{{ activeModel }}</span>
      </template>
      <template #actions>
        <button
          :class="['btn-toggle-sessions', { active: panelOpen }]"
          title="Toggle sidebar"
          @click="togglePanel"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <rect
              x="3"
              y="3"
              width="18"
              height="18"
              rx="2"
            /><line
              x1="15"
              y1="3"
              x2="15"
              y2="21"
            />
          </svg>
        </button>
      </template>
    </PageHeader>
    <div
      ref="chatBodyRef"
      :class="['chat-body', { 'editor-expanded': editorExpanded }]"
    >
      <!-- Left: Chat -->
      <div
        v-show="!editorExpanded"
        class="chat-main"
      >
        <div
          ref="chatArea"
          class="chat-messages"
          @scroll="onScroll"
        >
          <div
            v-if="messages.length === 0"
            class="welcome"
          >
            <div class="welcome-logo">
              ATRI
            </div>
            <div class="welcome-sub">
              AI Coding Agent &middot; Type a message to start
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
              v-if="item.type === 'tool'"
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
            v-if="sending && !thinkingBlock && Object.keys(toolCards).length === 0"
            class="thinking-indicator"
          >
            <span class="pulse-text">Thinking</span>
          </div>
        </div>
        <ChatInput
          :sending="sending"
          :agent-mode="agentMode"
          :mode-pending="modePending"
          @send="handleSend"
          @cancel="handleCancel"
          @set-mode="handleSetMode"
        />
      </div>

      <!-- Resize handle: chat <-> editor -->
      <div
        v-show="!editorExpanded"
        class="resize-handle"
        @pointerdown="startResize('editor', $event)"
      />

      <!-- Center: Editor Tabs -->
      <div
        :class="['editor-pane', { expanded: editorExpanded }]"
        :style="editorPaneStyle"
      >
        <EditorTabs
          ref="editorTabsRef"
          :expanded="editorExpanded"
          @request-expand="setEditorExpanded"
          @active-tab-type-change="handleActiveTabTypeChange"
        />
      </div>

      <Transition name="side-panel-slide">
        <div
          v-if="panelOpen && !editorExpanded"
          class="side-panel-wrap"
          :style="{ width: `${panelWidth}px` }"
        >
          <!-- Resize handle: editor <-> panel -->
          <div
            class="resize-handle panel-resize"
            @pointerdown="startResize('panel', $event)"
          />

          <!-- Right: Side Panel (Sessions / Files tabs) -->
          <aside class="side-panel">
            <div class="panel-tabs">
              <button
                :class="['panel-tab', { active: sideTab === 'sessions' }]"
                @click="sideTab = 'sessions'"
              >
                Sessions
              </button>
              <button
                :class="['panel-tab', { active: sideTab === 'files' }]"
                @click="sideTab = 'files'"
              >
                Files
              </button>
            </div>
            <div class="panel-content">
              <SessionPanel
                v-if="sideTab === 'sessions'"
                @open-workstation="handleOpenWorkstation"
              />
              <FilePanel
                v-else
                @open-file="handleOpenFile"
              />
            </div>
          </aside>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import ChatMessage from './ChatMessage.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCard from './ToolCard.vue'
import AgentTodoPanel from './AgentTodoPanel.vue'
import ChatInput from './ChatInput.vue'
import SessionPanel from './SessionPanel.vue'
import FilePanel from './FilePanel.vue'
import EditorTabs from './EditorTabs.vue'
import { useApi } from '@/composables/useApi.js'
import { buildChatDisplayItems } from '@/composables/chatDisplayItems.js'
import { useChat } from '@/composables/useChat.js'
import { useWebSocket } from '@/composables/useWebSocket.js'
import { useSession } from '@/composables/useSession.js'
import { useProviders } from '@/composables/useProviders.js'

const {
  messages, sending, thinkingBlock, toolCards,
  handleWsEvent, sendMessage, cancelMessage, clearThinking, clearToolCards,
  loadTranscript, resetMessages,
} = useChat()

const { currentId, loadSessionMessages, loadList } = useSession()
const { activeModel, loadProviders, loadStatus } = useProviders()
const { connected: wsConnected, events } = useWebSocket(currentId)
const api = useApi()

const chatArea = ref(null)
const chatBodyRef = ref(null)
const editorTabsRef = ref(null)
const agentMode = ref('agent')
const modePending = ref(false)
const panelOpen = ref(true)
const sideTab = ref('sessions')
const panelWidth = ref(280)
const editorWidth = ref(500)
const editorExpanded = ref(false)
const autoScroll = ref(true)
let handledEventCount = 0
let processingEvents = false
let resizeState = null

const CHAT_MIN_WIDTH = 340
const EDITOR_MIN_WIDTH = 280
const EDITOR_MAX_WIDTH = 1200
const PANEL_MIN_WIDTH = 200
const PANEL_MAX_WIDTH = 600
const HANDLE_SPACE = 4
const displayItems = computed(() => buildChatDisplayItems(messages.value))

const editorPaneStyle = computed(() => (
  editorExpanded.value
    ? { width: '100%' }
    : { width: `${editorWidth.value}px` }
))

function togglePanel() {
  panelOpen.value = !panelOpen.value
}

function setEditorExpanded(expanded) {
  editorExpanded.value = Boolean(expanded)
  nextTick(() => window.dispatchEvent(new Event('resize')))
}

function onScroll() {
  if (!chatArea.value) return
  const el = chatArea.value
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

function scrollToBottom() {
  nextTick(() => {
    if (chatArea.value && autoScroll.value) {
      chatArea.value.scrollTop = chatArea.value.scrollHeight
    }
  })
}

function clamp(value, min, max) {
  if (max < min) return min
  return Math.min(max, Math.max(min, value))
}

function startResize(target, event) {
  event.preventDefault()
  const bodyWidth = chatBodyRef.value?.clientWidth || window.innerWidth
  resizeState = {
    target,
    startX: event.clientX,
    startEditorWidth: editorWidth.value,
    startPanelWidth: panelWidth.value,
    startBodyWidth: bodyWidth,
  }
  window.addEventListener('pointermove', onResize)
  window.addEventListener('pointerup', stopResize)
  document.body.classList.add('is-resizing')
}

function onResize(event) {
  if (!resizeState) return
  const dx = event.clientX - resizeState.startX
  const bodyWidth = chatBodyRef.value?.clientWidth || resizeState.startBodyWidth
  if (resizeState.target === 'editor') {
    const maxEditorWidth = bodyWidth - CHAT_MIN_WIDTH - (panelOpen.value ? panelWidth.value : 0) - HANDLE_SPACE
    editorWidth.value = clamp(
      resizeState.startEditorWidth - dx,
      EDITOR_MIN_WIDTH,
      Math.min(EDITOR_MAX_WIDTH, maxEditorWidth),
    )
  } else {
    const totalWidth = resizeState.startEditorWidth + resizeState.startPanelWidth
    const minPanelWidth = Math.max(PANEL_MIN_WIDTH, totalWidth - EDITOR_MAX_WIDTH)
    const maxPanelWidth = Math.min(PANEL_MAX_WIDTH, totalWidth - EDITOR_MIN_WIDTH)
    const nextPanelWidth = clamp(resizeState.startPanelWidth - dx, minPanelWidth, maxPanelWidth)
    panelWidth.value = nextPanelWidth
    editorWidth.value = totalWidth - nextPanelWidth
  }
}

function stopResize() {
  resizeState = null
  window.removeEventListener('pointermove', onResize)
  window.removeEventListener('pointerup', stopResize)
  document.body.classList.remove('is-resizing')
}

function handleOpenFile(fileInfo) {
  if (editorTabsRef.value) {
    editorTabsRef.value.openFile(fileInfo)
  }
}

function handleOpenWorkstation() {
  if (editorTabsRef.value) {
    editorTabsRef.value.openWorkstation()
    setEditorExpanded(true)
  }
}

function handleActiveTabTypeChange(type) {
  if (type !== 'workstation' && editorExpanded.value) {
    setEditorExpanded(false)
  }
}

async function loadChatSession(id) {
  resetMessages()
  handledEventCount = events.value.length
  const transcript = await loadSessionMessages(id)
  if (transcript.messages.length || transcript.runtimeItems.length || transcript.todoSnapshot?.items?.length) {
    loadTranscript(transcript)
  }
  scrollToBottom()
}

async function handleSend(payload) {
  const text = typeof payload === 'string' ? payload : payload?.text || ''
  const images = Array.isArray(payload?.images) ? payload.images : []
  clearThinking()
  clearToolCards()
  await sendMessage(text, images)
  scrollToBottom()
  if (panelOpen.value && sideTab.value === 'sessions') loadList()
}

function handleCancel() {
  cancelMessage()
}

function normalizeMode(mode) {
  return mode === 'plan' ? 'plan' : 'agent'
}

async function loadAgentMode() {
  try {
    const data = await api.getAgentMode()
    agentMode.value = normalizeMode(data.mode)
  } catch {
    agentMode.value = 'agent'
  }
}

async function handleSetMode(mode) {
  const nextMode = normalizeMode(mode)
  if (agentMode.value === nextMode || modePending.value) return
  modePending.value = true
  try {
    const data = await api.setAgentMode(nextMode, 'dashboard mode switch')
    agentMode.value = normalizeMode(data.mode)
  } finally {
    modePending.value = false
  }
}

function onGlobalKeydown(e) {
  if (e.key === 'Escape' && sending.value) {
    e.preventDefault()
    handleCancel()
    return
  }
  if (e.key === 'c' && e.ctrlKey && sending.value) {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) {
      e.preventDefault()
      handleCancel()
    }
  }
}

async function processEvents() {
  if (processingEvents) return
  processingEvents = true
  try {
    while (handledEventCount < events.value.length) {
      const event = events.value[handledEventCount]
      handledEventCount++
      if (event.type === 'mode_changed') {
        agentMode.value = normalizeMode(event.mode)
      }
      handleWsEvent(event)
      scrollToBottom()
      await nextTick()
    }
  } finally {
    processingEvents = false
  }
}

watch(events, () => {
  processEvents()
}, { deep: false })

watch(messages, () => scrollToBottom(), { deep: true })

watch(currentId, async (newId, oldId) => {
  if (newId === oldId) return
  await loadChatSession(newId)
  loadList()
})

onMounted(async () => {
  window.addEventListener('keydown', onGlobalKeydown)
  await Promise.all([loadProviders(), loadStatus(), loadAgentMode()])
  await loadChatSession(currentId.value)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  stopResize()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: transparent;
}

.chat-body {
  flex: 1;
  display: flex;
  min-height: 0;
  min-width: 0;
  background: rgba(24, 24, 24, 0.12);
}

.chat-body.editor-expanded {
  background: #17191c;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 340px;
  background: rgba(24, 24, 24, 0.28);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 22px 22px 20px;
  scroll-behavior: smooth;
}

.welcome {
  text-align: center;
  padding: 80px 20px;
  color: var(--t3);
}

.welcome-logo {
  font-size: 36px;
  font-family: var(--mono);
  color: var(--t1);
  margin-bottom: 8px;
  font-weight: 700;
  letter-spacing: 0;
}

.welcome-sub {
  font-size: 14px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.status-dot.on {
  background: var(--ok);
  box-shadow: 0 0 10px rgba(143, 216, 199, 0.32);
}
.status-dot.off {
  background: var(--red);
}

.header-model {
  font-size: 12px;
  color: var(--t3);
  font-family: var(--mono);
}

/* Toggle sidebar button */
.btn-toggle-sessions {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 1px solid transparent;
  color: var(--t3);
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.12s;
}
.btn-toggle-sessions:hover {
  background: var(--bg-100);
  color: var(--t1);
  border-color: var(--border-light);
}
.btn-toggle-sessions.active {
  color: var(--acc2);
  background: var(--acc-bg);
  border-color: rgba(125, 168, 232, 0.3);
}

/* Center editor pane */
.editor-pane {
  flex-shrink: 0;
  min-width: 280px;
  min-height: 0;
  max-width: 1200px;
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: rgba(24, 24, 24, 0.48);
}

.editor-pane.expanded {
  flex: 1 1 auto;
  max-width: none;
  border-left: 0;
  border-right: 0;
}

/* Right side panel */
.side-panel-wrap {
  flex-shrink: 0;
  min-width: 0;
  max-width: 600px;
  display: flex;
  min-height: 0;
  overflow: hidden;
  will-change: width, opacity, transform;
}

.side-panel {
  flex: 1;
  min-width: 0;
  background: rgba(24, 24, 24, 0.56);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: rgba(24, 24, 24, 0.64);
  padding: 6px;
  gap: 4px;
}

.panel-tab {
  flex: 1;
  padding: 7px 10px;
  font-size: 12px;
  font-weight: 650;
  color: var(--t3);
  background: none;
  border: 1px solid transparent;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.12s;
}

.panel-tab:hover {
  color: var(--t2);
  background: var(--bg-050);
}

.panel-tab.active {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-strong);
}

.panel-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Resize handles */
.resize-handle {
  width: 4px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  position: relative;
  z-index: 2;
  margin: 0 -2px;
}

.resize-handle:hover,
.resize-handle:active {
  background: rgba(125, 168, 232, 0.16);
}

.panel-resize {
  margin-left: -2px;
  margin-right: 0;
}

.side-panel-slide-enter-active,
.side-panel-slide-leave-active {
  transition:
    width 0.24s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.18s ease,
    transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
}

.side-panel-slide-enter-from,
.side-panel-slide-leave-to {
  width: 0 !important;
  opacity: 0;
  transform: translateX(18px);
}

.side-panel-slide-enter-to,
.side-panel-slide-leave-from {
  opacity: 1;
  transform: translateX(0);
}

.side-panel-slide-enter-from .side-panel,
.side-panel-slide-leave-to .side-panel {
  opacity: 0.6;
}

:global(body.is-resizing) {
  cursor: col-resize;
  user-select: none;
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
