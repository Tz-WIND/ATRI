<template>
  <div class="app-shell">
    <ActivityBar
      :pages="navPages"
      :activePage="activePage"
      @navigate="navigateTo"
    />
    <div class="app-main">
      <div class="app-content" :class="{ 'has-player': hasPlayer }">
        <KeepAlive>
          <component :is="activeComponent" :key="activePage" />
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
import { ref, computed, markRaw, onMounted, onUnmounted } from 'vue'
import ActivityBar from './components/activity/ActivityBar.vue'
import ChatPage from './components/chat/ChatPage.vue'
import WorkspacePage from './components/pages/WorkspacePage.vue'
import AdaptersPage from './components/pages/AdaptersPage.vue'
import McpPage from './components/pages/McpPage.vue'
import SkillsPage from './components/pages/SkillsPage.vue'
import SettingsPage from './components/settings/SettingsPage.vue'
import MusicPage from './components/music/MusicPage.vue'
import MusicPlayer from './components/music/MusicPlayer.vue'
import MusicFullPlayer from './components/music/MusicFullPlayer.vue'
import { useMusic } from './composables/useMusic.js'

const { currentSong, playerCollapsed, handleWsControl } = useMusic()

const navPages = [
  { id: 'chat', label: 'Chat', icon: 'chat' },
  { id: 'music', label: 'Music', icon: 'music' },
  { id: 'workspace', label: 'Workspace', icon: 'folder' },
  { id: 'adapters', label: 'Adapters', icon: 'plug' },
  { id: 'mcp', label: 'MCP', icon: 'server' },
  { id: 'skills', label: 'Skills', icon: 'wand' },
  { id: 'settings', label: 'Settings', icon: 'gear' },
]

const activePage = ref('chat')

const pageMap = {
  chat: markRaw(ChatPage),
  music: markRaw(MusicPage),
  workspace: markRaw(WorkspacePage),
  adapters: markRaw(AdaptersPage),
  mcp: markRaw(McpPage),
  skills: markRaw(SkillsPage),
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
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${protocol}//${location.host}/ws`)
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === 'music_control') {
        handleWsControl(msg)
      }
    } catch {}
  }
  ws.onclose = () => {
    wsRetryTimer = setTimeout(connectWs, 3000)
  }
  ws.onerror = () => ws.close()
}

onMounted(() => {
  connectWs()
})

onUnmounted(() => {
  clearTimeout(wsRetryTimer)
  if (ws) ws.close()
})
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  background: var(--bg0);
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.app-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.app-content.has-player {
  padding-bottom: 67px;
}
</style>
