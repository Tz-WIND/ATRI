import { ref } from 'vue'
import { useApi } from './useApi.js'

const STORAGE_KEY = 'atri_session'

function normalizeSessionId(id) {
  const prefix = 'webchat:friend:'
  return id.startsWith(prefix) ? id.slice(prefix.length) : id
}

function displayName(id) {
  id = normalizeSessionId(id)
  if (id === 'webchat_default') return 'Default'
  return id.replace(/^webchat_/, '')
}

let instance = null

export function useSession() {
  if (instance) return instance

  const api = useApi()
  const currentId = ref(localStorage.getItem(STORAGE_KEY) || 'webchat_default')
  const sessions = ref([])
  const loading = ref(false)

  async function loadList() {
    try {
      sessions.value = await api.getSessions()
    } catch {
      sessions.value = []
    }
  }

  async function switchSession(id) {
    currentId.value = id
    localStorage.setItem(STORAGE_KEY, id)
  }

  function createNew() {
    const id = 'webchat_' + Date.now().toString(36)
    switchSession(id)
    return id
  }

  async function removeSession(id) {
    await api.deleteSession(id)
    if (currentId.value === id) {
      createNew()
    } else {
      await loadList()
    }
  }

  async function loadSessionMessages(id) {
    try {
      const data = await api.getSession(id)
      return {
        messages: data.messages || [],
        runtimeTurns: data.runtime_turns || [],
        runtimeItems: data.runtime_items || [],
        todoSnapshot: data.todo_snapshot || null,
      }
    } catch {
      return { messages: [], runtimeTurns: [], runtimeItems: [], todoSnapshot: null }
    }
  }

  instance = {
    currentId,
    sessions,
    loading,
    loadList,
    switchSession,
    createNew,
    removeSession,
    loadSessionMessages,
    normalizeSessionId,
    displayName,
  }
  return instance
}
