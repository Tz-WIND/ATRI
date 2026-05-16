import { markSetupRequired, markUnauthenticated } from './useAuth.js'

const BASE = ''

async function request(url, options = {}) {
  const { headers, ...rest } = options
  const res = await fetch(BASE + url, {
    ...rest,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    if (res.status === 428 && body.setup_required) {
      markSetupRequired(body.error || 'setup required')
    }
    if (res.status === 401) {
      markUnauthenticated(body.error || 'authentication required')
    }
    throw new Error(body.error || `HTTP ${res.status}`)
  }
  return res.json()
}

export function useApi() {
  return {
    // Status
    getStatus: () => request('/api/status'),
    getConfigSchema: () => request('/api/config/schema'),

    // Settings
    getSettings: () => request('/api/settings'),
    saveSettings: (data) => request('/api/settings', { method: 'POST', body: JSON.stringify(data) }),
    audioDevices: () => request('/api/audio/devices'),

    // Providers
    getProviders: () => request('/api/provider/list'),
    saveProvider: (data) => request('/api/provider/save', { method: 'POST', body: JSON.stringify(data) }),
    deleteProvider: (name) => request('/api/provider/delete', { method: 'POST', body: JSON.stringify({ name }) }),
    fetchProviderModels: (data) => request('/api/provider/models', {
      method: 'POST',
      body: JSON.stringify(typeof data === 'string' ? { name: data } : data),
    }),
    activateModel: (provider, model) => request('/api/provider/activate', { method: 'POST', body: JSON.stringify({ provider, model }) }),
    deactivateModel: (provider, model) => request('/api/provider/deactivate', { method: 'POST', body: JSON.stringify({ provider, model }) }),
    selectModel: (provider, model) => request('/api/provider/select', { method: 'POST', body: JSON.stringify({ provider, model }) }),

    // Workspace
    getWorkspace: () => request('/api/workspace'),
    saveWorkspace: (workspace) => request('/api/workspace', { method: 'POST', body: JSON.stringify({ workspace }) }),

    // Adapter
    getAdapter: () => request('/api/adapter'),
    saveAdapter: (data) => request('/api/adapter', { method: 'POST', body: JSON.stringify(data) }),

    // MCP
    getMcpServers: () => request('/api/mcp/servers'),
    getMcpStatus: () => request('/api/mcp/status'),
    reloadMcp: () => request('/api/mcp/reload', { method: 'POST' }),
    saveMcpServer: (data) => request('/api/mcp/servers', { method: 'POST', body: JSON.stringify(data) }),
    updateMcpServer: (name, data) => request(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
    validateMcpServer: (name) => request(`/api/mcp/servers/${encodeURIComponent(name)}/validate`, { method: 'POST' }),
    reloadMcpServer: (name) => request(`/api/mcp/servers/${encodeURIComponent(name)}/reload`, { method: 'POST' }),
    deleteMcpServer: (name) => request(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: 'DELETE' }),

    // Skills
    getSkills: () => request('/api/skills'),
    getSkill: (name) => request(`/api/skills/${encodeURIComponent(name)}`),
    updateSkill: (name, data) => request(`/api/skills/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteSkill: (name) => request(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    uploadSkill: (file) => {
      const formData = new FormData()
      formData.append('file', file)
      return fetch(BASE + '/api/skills/upload', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData,
      }).then(async res => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          if (res.status === 428 && body.setup_required) {
            markSetupRequired(body.error || 'setup required')
          }
          if (res.status === 401) {
            markUnauthenticated(body.error || 'authentication required')
          }
          throw new Error(body.error || `HTTP ${res.status}`)
        }
        return res.json()
      })
    },

    // Sessions
    getSessions: () => request('/api/sessions'),
    getSession: (id) => request(`/api/sessions/${encodeURIComponent(id)}`),
    deleteSession: (id) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),

    // Chat
    sendMessage: (message, sessionId, images = []) => request('/api/chat', { method: 'POST', body: JSON.stringify({ message, session_id: sessionId, images }) }),
    cancelChat: (sessionId) => request('/api/chat/cancel', { method: 'POST', body: JSON.stringify({ session_id: sessionId || '' }) }),
    getAgentMode: () => request('/api/agent-mode'),
    setAgentMode: (mode, reason = '') => request('/api/agent-mode', { method: 'POST', body: JSON.stringify({ mode, reason }) }),
    getTools: () => request('/api/tools'),
    approveCommand: (sessionId) => request('/api/approve-command', { method: 'POST', body: JSON.stringify({ session_id: sessionId }) }),

    // Files
    listFiles: (path) => request(`/api/filelist?path=${encodeURIComponent(path || '')}`),
    readFile: (path) => request(`/api/fileread?path=${encodeURIComponent(path)}`),
    writeFile: (path, content) => request('/api/filewrite', { method: 'POST', body: JSON.stringify({ path, content }) }),

    // Music
    musicDirs: () => request('/api/music/dirs'),
    saveMusicDirs: (directories) => request('/api/music/dirs', { method: 'POST', body: JSON.stringify({ directories }) }),
    musicScan: () => request('/api/music/scan', { method: 'POST' }),
    musicLibrary: () => request('/api/music/library'),

    // AI Music Workstation
    studioProject: () => request('/api/music/studio/project'),
    saveStudioProject: (project, options = {}) => request('/api/music/studio/project', {
      method: 'PUT',
      body: JSON.stringify({ project, ...options }),
    }),
    studioDemo: (options = {}) => request('/api/music/studio/demo', {
      method: 'POST',
      body: JSON.stringify(options),
    }),
    hostStatus: () => request('/api/music/studio/host/status'),
    hostStart: (options = {}) => request('/api/music/studio/host/start', {
      method: 'POST',
      body: JSON.stringify(options),
    }),
    hostCommand: (cmd, params = {}) => request('/api/music/studio/host/command', {
      method: 'POST',
      body: JSON.stringify({ cmd, params }),
    }),
    studioPlugins: (options = null) => options
      ? request('/api/music/studio/plugins', {
        method: 'POST',
        body: JSON.stringify(options),
      })
      : request('/api/music/studio/plugins'),
    studioSync: (options = {}) => request('/api/music/studio/sync', {
      method: 'POST',
      body: JSON.stringify(options),
    }),
    studioTransport: (action, payload = {}) => request('/api/music/studio/transport', {
      method: 'POST',
      body: JSON.stringify({ action, ...payload }),
    }),
    studioMidiWrite: (payload) => request('/api/music/studio/midi/write', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    studioMidiDiff: (payload) => request('/api/music/studio/midi/diff', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    studioCreateTrack: (name) => request('/api/music/studio/tracks', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
    studioUpdateTrack: (trackId, data) => request(`/api/music/studio/tracks/${encodeURIComponent(trackId)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
    studioSetTrackPlugin: (trackId, plugin, slotId = 'instrument') => request(`/api/music/studio/tracks/${encodeURIComponent(trackId)}/plugin`, {
      method: 'POST',
      body: JSON.stringify({ plugin, slot_id: slotId }),
    }),
    studioOpenPluginEditor: (trackId, slotId = 'instrument') => request(`/api/music/studio/tracks/${encodeURIComponent(trackId)}/plugin/editor`, {
      method: 'POST',
      body: JSON.stringify({ slot_id: slotId }),
    }),
  }
}
