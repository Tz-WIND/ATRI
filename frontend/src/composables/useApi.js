const BASE = ''

async function request(url, options = {}) {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `HTTP ${res.status}`)
  }
  return res.json()
}

export function useApi() {
  return {
    // Status
    getStatus: () => request('/api/status'),

    // Settings
    getSettings: () => request('/api/settings'),
    saveSettings: (data) => request('/api/settings', { method: 'POST', body: JSON.stringify(data) }),

    // Providers
    getProviders: () => request('/api/provider/list'),
    saveProvider: (data) => request('/api/provider/save', { method: 'POST', body: JSON.stringify(data) }),
    deleteProvider: (name) => request('/api/provider/delete', { method: 'POST', body: JSON.stringify({ name }) }),
    fetchProviderModels: (name) => request('/api/provider/models', { method: 'POST', body: JSON.stringify({ name }) }),
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
    saveMcpServer: (data) => request('/api/mcp/servers', { method: 'POST', body: JSON.stringify(data) }),
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
        body: formData,
      }).then(async res => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
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
    sendMessage: (message, sessionId) => request('/api/chat', { method: 'POST', body: JSON.stringify({ message, session_id: sessionId }) }),
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
  }
}
