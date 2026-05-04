<template>
  <div class="page">
    <PageHeader title="MCP Servers">
      <template #actions>
        <button class="btn btn-ghost" @click="showForm = !showForm">
          {{ showForm ? 'Cancel' : '+ Add Server' }}
        </button>
      </template>
    </PageHeader>
    <div class="page-body">
      <!-- Add form -->
      <div v-if="showForm" class="card">
        <section class="form-section">
          <h3>Add MCP Server</h3>
          <div class="field">
            <label>Name</label>
            <input v-model="form.name" placeholder="my-server" />
          </div>
          <div class="field">
            <label>Type</label>
            <select v-model="form.transport" @change="onTypeChange">
              <option value="stdio">Stdio</option>
              <option value="sse">SSE</option>
              <option value="streamable_http">Streamable HTTP</option>
            </select>
          </div>
          <template v-if="form.transport === 'stdio'">
            <div class="field">
              <label>Command</label>
              <input v-model="form.command" placeholder="npx" />
            </div>
            <div class="field">
              <label>Args (comma separated)</label>
              <input v-model="form.argsStr" placeholder="-y, @some/mcp-server" />
            </div>
          </template>
          <template v-else>
            <div class="field">
              <label>URL</label>
              <input v-model="form.url" placeholder="http://localhost:3000/mcp" />
            </div>
          </template>
          <div class="field">
            <label>Active</label>
            <select v-model="form.active">
              <option :value="true">Yes</option>
              <option :value="false">No</option>
            </select>
          </div>
          <div class="form-actions">
            <button class="btn btn-primary" @click="saveServer">Save</button>
            <button class="btn btn-ghost" @click="showForm = false">Cancel</button>
          </div>
        </section>
      </div>

      <!-- Server list -->
      <div v-if="servers.length === 0" class="empty">No MCP servers configured.</div>
      <div v-for="s in servers" :key="s.name" class="card">
        <div class="card-header">
          <span class="card-title">{{ s.name }}</span>
          <StatusBadge :type="s.active ? 'on' : 'off'">{{ s.active ? 'active' : 'inactive' }}</StatusBadge>
        </div>
        <div class="card-meta">
          {{ s.command ? `stdio: ${s.command}` : s.url || '' }}
        </div>
        <div class="card-actions">
          <button class="btn btn-ghost" @click="deleteServer(s.name)">Delete</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const servers = ref([])
const showForm = ref(false)

const blankForm = () => ({
  name: '', transport: 'stdio', command: '', argsStr: '', url: '', active: true,
})

const form = ref(blankForm())

function onTypeChange() {
  // Form fields are reactive automatically
}

onMounted(loadServers)

async function loadServers() {
  try {
    servers.value = await api.getMcpServers()
  } catch { servers.value = [] }
}

async function saveServer() {
  if (!form.value.name.trim()) return
  const data = {
    name: form.value.name,
    active: form.value.active,
    transport: form.value.transport,
  }
  if (form.value.transport === 'stdio') {
    data.command = form.value.command
    data.args = form.value.argsStr.split(',').map(s => s.trim()).filter(Boolean)
  } else {
    data.url = form.value.url
  }
  await api.saveMcpServer(data)
  form.value = blankForm()
  showForm.value = false
  await loadServers()
}

async function deleteServer(name) {
  await api.deleteMcpServer(name)
  await loadServers()
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 20px; }

.form-section { margin-bottom: 24px; }
.form-section h3 {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--t3); margin-bottom: 12px; font-family: var(--mono);
}

.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--t2); margin-bottom: 4px; font-family: var(--mono); }
.field input, .field select {
  width: 100%; background: var(--bg0); border: 1px solid var(--border);
  border-radius: 6px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field input:focus, .field select:focus { border-color: var(--acc); }

.card {
  background: var(--bg1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; margin-bottom: 10px;
}

.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.card-title { font-size: 13px; font-weight: 600; font-family: var(--mono); color: var(--t1); }
.card-meta { font-size: 11px; color: var(--t3); }
.card-actions { margin-top: 8px; display: flex; gap: 6px; }

.empty { color: var(--t3); font-size: 13px; }

.form-actions { display: flex; gap: 8px; margin-top: 8px; }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn-primary { background: rgba(63,185,80,0.15); color: var(--green); border-color: rgba(63,185,80,0.3); }
.btn-primary:hover { background: rgba(63,185,80,0.25); }
.btn-ghost { background: none; color: var(--t2); }
.btn-ghost:hover { background: var(--bg2); color: var(--t1); }
</style>
