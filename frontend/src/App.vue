<template>
  <div
    v-if="auth.loading"
    class="auth-loading"
  >
    <div class="auth-loading-panel">
      <div
        class="auth-loading-logo"
        aria-hidden="true"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 17l10 5 10-5" />
          <path d="M2 12l10 5 10-5" />
        </svg>
      </div>
      <span>Checking authorization...</span>
    </div>
  </div>
  <AuthGate v-else-if="(auth.authRequired || auth.setupRequired) && !auth.authenticated" />
  <div
    v-else
    class="app-shell"
  >
    <ActivityBar
      :pages="navPages"
      :active-page="activePage"
      @navigate="navigateTo"
    />
    <div class="app-main">
      <div
        class="app-content"
        :class="{ 'has-player': hasPlayer }"
      >
        <KeepAlive>
          <component
            :is="activeComponent"
            :key="activePage"
          />
        </KeepAlive>
      </div>
    </div>

    <!-- Persistent bottom player bar -->
    <MusicPlayer />
    <!-- Full-screen player overlay -->
    <MusicFullPlayer />
  </div>
</template>

<script setup>
import { ref, computed, markRaw, onMounted, onUnmounted, watch } from 'vue'
import ActivityBar from './components/activity/ActivityBar.vue'
import AuthGate from './components/auth/AuthGate.vue'
import ChatPage from './components/chat/ChatPage.vue'
import WorkspacePage from './components/pages/WorkspacePage.vue'
import AdaptersPage from './components/pages/AdaptersPage.vue'
import McpPage from './components/pages/McpPage.vue'
import SkillsPage from './components/pages/SkillsPage.vue'
import KnowledgePage from './components/pages/KnowledgePage.vue'
import SettingsPage from './components/settings/SettingsPage.vue'
import MusicStudio from './components/music/MusicStudio.vue'
import MusicPage from './components/music/MusicPage.vue'
import MusicPlayer from './components/music/MusicPlayer.vue'
import MusicFullPlayer from './components/music/MusicFullPlayer.vue'
import { useAuth } from './composables/useAuth.js'
import { useDawHost } from './composables/useDawHost.js'
import { useMusic } from './composables/useMusic.js'
import { clearWsInstance } from './composables/useWebSocket.js'

const { auth, initAuth } = useAuth()
const { currentSong, playerCollapsed, handleWsControl } = useMusic()
const { handleProjectBroadcast } = useDawHost()

const navPages = [
  { id: 'chat', label: 'Chat', icon: 'chat' },
  { id: 'studio', label: 'Studio', icon: 'studio' },
  { id: 'music', label: 'Music', icon: 'music' },
  { id: 'workspace', label: 'Workspace', icon: 'folder' },
  { id: 'adapters', label: 'Adapters', icon: 'plug' },
  { id: 'mcp', label: 'MCP', icon: 'server' },
  { id: 'skills', label: 'Skills', icon: 'wand' },
  { id: 'knowledge', label: 'Knowledge', icon: 'knowledge' },
  { id: 'settings', label: 'Settings', icon: 'gear' },
]

const activePage = ref('chat')

const pageMap = {
  chat: markRaw(ChatPage),
  studio: markRaw(MusicStudio),
  music: markRaw(MusicPage),
  workspace: markRaw(WorkspacePage),
  adapters: markRaw(AdaptersPage),
  mcp: markRaw(McpPage),
  skills: markRaw(SkillsPage),
  knowledge: markRaw(KnowledgePage),
  settings: markRaw(SettingsPage),
}

const activeComponent = computed(() => pageMap[activePage.value])
const hasPlayer = computed(() => !!currentSong.value && !playerCollapsed.value)

function navigateTo(id) {
  activePage.value = id
}

// WebSocket listener for AI agent music control
let ws = null
let wsRetryTimer = null

function connectWs() {
  if (ws || !auth.authenticated) return
  clearTimeout(wsRetryTimer)
  wsRetryTimer = null
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${protocol}//${location.host}/ws`)
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === 'music_control') {
        handleWsControl(msg)
      } else if (msg.type === 'music_project') {
        handleProjectBroadcast(msg)
      }
    } catch {}
  }
  ws.onclose = () => {
    ws = null
    if (auth.authenticated) {
      wsRetryTimer = setTimeout(connectWs, 3000)
    }
  }
  ws.onerror = () => {
    if (ws) ws.close()
  }
}

function disconnectWs() {
  clearTimeout(wsRetryTimer)
  wsRetryTimer = null
  clearWsInstance()
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
}

onMounted(() => {
  initAuth().then(() => {
    if (auth.authenticated) connectWs()
  }).catch(() => {})
})

watch(() => auth.authenticated, (authenticated) => {
  if (authenticated) {
    connectWs()
  } else {
    disconnectWs()
  }
})

onUnmounted(() => {
  disconnectWs()
})
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  background: var(--app-bg);
  color: var(--t1);
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 8px 8px 8px 0;
}

.app-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: rgba(24, 24, 24, 0.72);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-soft);
  backdrop-filter: blur(18px);
}

.app-content.has-player {
  padding-bottom: 67px;
}

.auth-loading {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
  color: var(--t2);
}

.auth-loading-panel {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--glass);
  box-shadow: var(--shadow-soft);
  font-family: var(--mono);
  font-size: 12px;
}

.auth-loading-logo {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--acc2);
  background: var(--acc-bg);
  border: 1px solid rgba(125, 168, 232, 0.22);
  border-radius: var(--radius-sm);
}

.auth-loading-logo svg {
  width: 22px;
  height: 22px;
}
</style>
