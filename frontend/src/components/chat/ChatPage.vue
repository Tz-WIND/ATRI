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
    <div class="chat-body">
      <!-- Left: Chat -->
      <div class="chat-main">
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
            v-for="msg in messages"
            :key="msg.id"
          >
            <ToolCard
              v-if="msg.role === 'tool'"
              :tool-data="msg.toolData"
            />
            <ThinkingBlock
              v-else-if="msg.role === 'thinking'"
              :thinking="msg"
            />
            <ChatMessage
              v-else
              :message="msg"
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
          @send="handleSend"
          @cancel="handleCancel"
        />
      </div>

      <!-- Resize handle: chat <-> editor -->
      <div
        class="resize-handle"
        @pointerdown="startResize('editor', $event)"
      />

      <!-- Center: Editor Tabs -->
      <div
        class="editor-pane"
        :style="{ width: `${editorWidth}px` }"
      >
        <EditorTabs ref="editorTabsRef" />
      </div>

      <!-- Resize handle: editor <-> panel -->
      <div
        v-if="panelOpen"
        class="resize-handle"
        @pointerdown="startResize('panel', $event)"
      />

      <!-- Right: Side Panel (Sessions / Files tabs) -->
      <aside
        v-if="panelOpen"
        class="side-panel"
        :style="{ width: `${panelWidth}px` }"
      >
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
          <SessionPanel v-if="sideTab === 'sessions'" />
          <FilePanel
            v-else
            @open-file="handleOpenFile"
          />
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import ChatMessage from './ChatMessage.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCard from './ToolCard.vue'
import ChatInput from './ChatInput.vue'
import SessionPanel from './SessionPanel.vue'
import FilePanel from './FilePanel.vue'
import EditorTabs from './EditorTabs.vue'
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

const chatArea = ref(null)
const editorTabsRef = ref(null)
const panelOpen = ref(true)
const sideTab = ref('files')
const panelWidth = ref(280)
const editorWidth = ref(500)
const autoScroll = ref(true)
let handledEventCount = 0
let processingEvents = false
let resizeState = null

function togglePanel() {
  panelOpen.value = !panelOpen.value
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
  return Math.min(max, Math.max(min, value))
}

function startResize(target, event) {
  resizeState = {
    target,
    startX: event.clientX,
    startEditorWidth: editorWidth.value,
    startPanelWidth: panelWidth.value,
  }
  window.addEventListener('pointermove', onResize)
  window.addEventListener('pointerup', stopResize)
  document.body.classList.add('is-resizing')
}

function onResize(event) {
  if (!resizeState) return
  const dx = event.clientX - resizeState.startX
  if (resizeState.target === 'editor') {
    editorWidth.value = clamp(resizeState.startEditorWidth - dx, 280, 1200)
  } else {
    panelWidth.value = clamp(resizeState.startPanelWidth - dx, 200, 600)
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

async function loadChatSession(id) {
  resetMessages()
  handledEventCount = events.value.length
  const msgs = await loadSessionMessages(id)
  if (msgs.length) {
    loadTranscript(msgs)
  }
  scrollToBottom()
}

async function handleSend(text) {
  clearThinking()
  clearToolCards()
  await sendMessage(text)
  scrollToBottom()
  if (panelOpen.value && sideTab.value === 'sessions') loadList()
}

function handleCancel() {
  cancelMessage()
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
  await Promise.all([loadProviders(), loadStatus()])
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
}

.chat-body {
  flex: 1;
  display: flex;
  min-height: 0;
  min-width: 0;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 260px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  scroll-behavior: smooth;
}

.welcome {
  text-align: center;
  padding: 80px 20px;
  color: var(--t3);
}

.welcome-logo {
  font-size: 42px;
  font-family: var(--mono);
  color: var(--acc);
  margin-bottom: 8px;
  font-weight: 700;
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
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
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
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 1px solid transparent;
  color: var(--t3);
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.12s;
}
.btn-toggle-sessions:hover {
  background: var(--bg2);
  color: var(--t1);
  border-color: var(--border);
}
.btn-toggle-sessions.active {
  color: var(--acc2);
  background: var(--acc-bg);
  border-color: rgba(0, 122, 204, 0.3);
}

/* Center editor pane */
.editor-pane {
  flex-shrink: 0;
  min-width: 280px;
  max-width: 1200px;
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}

/* Right side panel */
.side-panel {
  flex-shrink: 0;
  min-width: 200px;
  max-width: 600px;
  background: var(--bg1);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg1);
}

.panel-tab {
  flex: 1;
  padding: 8px 12px;
  font-size: 12px;
  font-family: var(--mono);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--t3);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.12s;
}

.panel-tab:hover {
  color: var(--t2);
  background: var(--bg2);
}

.panel-tab.active {
  color: var(--t1);
  border-bottom-color: var(--acc2);
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
  background: var(--acc-bg);
}

:global(body.is-resizing) {
  cursor: col-resize;
  user-select: none;
}

.thinking-indicator {
  text-align: center;
  padding: 12px;
}

.pulse-text {
  color: var(--t3);
  font-size: 14px;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
</style>
