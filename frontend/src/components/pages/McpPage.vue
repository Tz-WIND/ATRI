<template>
  <div class="page">
    <PageHeader title="MCP Servers">
      <template #status>
        <StatusBadge
          type="info"
          dot
        >
          {{ summary.connected }}/{{ summary.active }} connected
        </StatusBadge>
      </template>
      <template #actions>
        <button
          class="btn btn-ghost"
          :disabled="reloading"
          @click="reloadAll"
        >
          {{ reloading ? 'Reloading' : 'Reload' }}
        </button>
        <button
          class="btn btn-ghost"
          @click="showForm = !showForm"
        >
          {{ showForm ? 'Cancel' : '+ Add Server' }}
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <div class="summary-strip">
        <div class="metric">
          <span class="metric-value">{{ summary.toolAdapters }}</span>
          <span class="metric-label">tool adapters</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ summary.tools }}</span>
          <span class="metric-label">native tools</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ summary.resources }}</span>
          <span class="metric-label">resources</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ summary.prompts }}</span>
          <span class="metric-label">prompts</span>
        </div>
      </div>

      <div
        v-if="error"
        class="notice error"
      >
        {{ error }}
      </div>

      <div
        v-if="showForm"
        class="card"
      >
        <section class="form-section">
          <h3>Add MCP Server</h3>
          <div class="field">
            <label>Name</label>
            <input
              v-model.trim="form.name"
              placeholder="filesystem"
              spellcheck="false"
            >
          </div>
          <div class="field">
            <label>Transport</label>
            <select v-model="form.transport">
              <option value="stdio">
                stdio
              </option>
              <option value="streamable_http">
                streamable_http
              </option>
            </select>
          </div>
          <template v-if="form.transport === 'stdio'">
            <div class="field">
              <label>Command</label>
              <input
                v-model.trim="form.command"
                placeholder="npx"
                spellcheck="false"
              >
            </div>
            <div class="field">
              <label>Args</label>
              <input
                v-model="form.argsStr"
                placeholder="-y, @modelcontextprotocol/server-filesystem, ./workspace"
                spellcheck="false"
              >
            </div>
            <div class="field">
              <label>CWD</label>
              <input
                v-model.trim="form.cwd"
                placeholder="./workspace"
                spellcheck="false"
              >
            </div>
          </template>
          <template v-else>
            <div class="field">
              <label>URL</label>
              <input
                v-model.trim="form.url"
                placeholder="http://localhost:3000/mcp"
                spellcheck="false"
              >
            </div>
          </template>
          <div class="inline-fields">
            <div class="field">
              <label>Timeout</label>
              <input
                v-model.number="form.timeout"
                type="number"
                min="1"
                max="120"
              >
            </div>
            <div class="field">
              <label>Active</label>
              <select v-model="form.active">
                <option :value="true">
                  yes
                </option>
                <option :value="false">
                  no
                </option>
              </select>
            </div>
          </div>
          <div class="form-actions">
            <button
              class="btn btn-primary"
              :disabled="saving"
              @click="saveServer"
            >
              {{ saving ? 'Saving' : 'Save' }}
            </button>
            <button
              class="btn btn-ghost"
              @click="showForm = false"
            >
              Cancel
            </button>
          </div>
        </section>
      </div>

      <div
        v-if="!loading && servers.length === 0"
        class="empty"
      >
        No MCP servers configured.
      </div>

      <div
        v-for="server in servers"
        :key="server.name"
        class="card server-card"
      >
        <div class="card-header">
          <div class="title-stack">
            <span class="card-title">{{ server.name }}</span>
            <span class="card-meta">{{ endpointLabel(server) }}</span>
          </div>
          <StatusBadge
            :type="statusType(server)"
            dot
          >
            {{ statusLabel(server) }}
          </StatusBadge>
        </div>

        <div class="inventory">
          <span>{{ toolAdapters(server) }} tools</span>
          <span>{{ resourceCount(server) }} resources</span>
          <span>{{ server.prompts?.length || 0 }} prompts</span>
          <span v-if="server.protocol_version">{{ server.protocol_version }}</span>
        </div>

        <div
          v-if="server.error"
          class="notice error compact"
        >
          {{ server.error }}
        </div>
        <div
          v-if="validation[server.name]?.error"
          class="notice error compact"
        >
          {{ validation[server.name].error }}
        </div>
        <div
          v-else-if="validation[server.name]?.status === 'connected'"
          class="notice ok compact"
        >
          validation connected
        </div>

        <div
          v-if="server.tools?.length"
          class="section"
        >
          <div class="section-title">
            Tools
          </div>
          <div class="rows">
            <div
              v-for="tool in server.tools"
              :key="tool.registered_name"
              class="row"
            >
              <span class="row-name">{{ tool.registered_name }}</span>
              <span class="row-meta">{{ tool.name }}</span>
            </div>
          </div>
        </div>

        <div
          v-if="resourceCount(server)"
          class="section"
        >
          <div class="section-title">
            Resources
          </div>
          <div class="chips">
            <span
              v-for="resource in [...(server.resources || []), ...(server.resource_templates || [])]"
              :key="resource.uri || resource.uriTemplate || resource.name"
              class="chip"
            >
              {{ resourceLabel(resource) }}
            </span>
          </div>
        </div>

        <div
          v-if="server.prompts?.length"
          class="section"
        >
          <div class="section-title">
            Prompts
          </div>
          <div class="chips">
            <span
              v-for="prompt in server.prompts"
              :key="prompt.name"
              class="chip"
            >
              {{ prompt.name }}
            </span>
          </div>
        </div>

        <div class="card-actions">
          <button
            class="btn btn-ghost"
            :disabled="validating[server.name]"
            @click="validateServer(server.name)"
          >
            {{ validating[server.name] ? 'Validating' : 'Validate' }}
          </button>
          <button
            class="btn btn-ghost"
            :disabled="reloading"
            @click="reloadServer(server.name)"
          >
            Reload
          </button>
          <button
            class="btn btn-danger"
            @click="deleteServer(server.name)"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const servers = ref([])
const loading = ref(false)
const reloading = ref(false)
const saving = ref(false)
const showForm = ref(false)
const error = ref('')
const validating = ref({})
const validation = ref({})

const blankForm = () => ({
  name: '',
  transport: 'stdio',
  command: '',
  argsStr: '',
  cwd: '',
  url: '',
  timeout: 20,
  active: true,
})

const form = ref(blankForm())

const summary = computed(() => {
  const active = servers.value.filter(server => server.active !== false).length
  const connected = servers.value.filter(server => server.status === 'connected').length
  const nativeTools = servers.value.reduce((sum, server) => sum + (server.tools?.length || 0), 0)
  const resourceTools = servers.value.filter(server => resourceCount(server) > 0).length
  const promptTools = servers.value.filter(server => (server.prompts?.length || 0) > 0).length
  return {
    active,
    connected,
    tools: nativeTools,
    toolAdapters: nativeTools + resourceTools + promptTools,
    resources: servers.value.reduce((sum, server) => sum + resourceCount(server), 0),
    prompts: servers.value.reduce((sum, server) => sum + (server.prompts?.length || 0), 0),
  }
})

onMounted(loadServers)

async function loadServers() {
  loading.value = true
  error.value = ''
  try {
    servers.value = await api.getMcpServers()
  } catch (err) {
    servers.value = []
    error.value = err.message || 'failed to load MCP servers'
  } finally {
    loading.value = false
  }
}

async function saveServer() {
  const name = form.value.name.trim()
  if (!name) return
  saving.value = true
  error.value = ''
  try {
    const data = {
      name,
      active: form.value.active,
      transport: form.value.transport,
      timeout: Number(form.value.timeout) || 20,
    }
    if (form.value.transport === 'stdio') {
      data.command = form.value.command
      data.args = form.value.argsStr.split(',').map(item => item.trim()).filter(Boolean)
      if (form.value.cwd) data.cwd = form.value.cwd
    } else {
      data.url = form.value.url
    }
    await api.saveMcpServer(data)
    form.value = blankForm()
    showForm.value = false
    await loadServers()
  } catch (err) {
    error.value = err.message || 'failed to save MCP server'
  } finally {
    saving.value = false
  }
}

async function reloadAll() {
  reloading.value = true
  error.value = ''
  try {
    await api.reloadMcp()
    await loadServers()
  } catch (err) {
    error.value = err.message || 'failed to reload MCP servers'
  } finally {
    reloading.value = false
  }
}

async function reloadServer(name) {
  reloading.value = true
  error.value = ''
  try {
    await api.reloadMcpServer(name)
    await loadServers()
  } catch (err) {
    error.value = err.message || 'failed to reload MCP server'
  } finally {
    reloading.value = false
  }
}

async function validateServer(name) {
  validating.value = { ...validating.value, [name]: true }
  validation.value = { ...validation.value, [name]: null }
  try {
    const result = await api.validateMcpServer(name)
    validation.value = { ...validation.value, [name]: result }
  } catch (err) {
    validation.value = { ...validation.value, [name]: { status: 'error', error: err.message } }
  } finally {
    validating.value = { ...validating.value, [name]: false }
  }
}

async function deleteServer(name) {
  error.value = ''
  try {
    await api.deleteMcpServer(name)
    validation.value = { ...validation.value, [name]: null }
    await loadServers()
  } catch (err) {
    error.value = err.message || 'failed to delete MCP server'
  }
}

function endpointLabel(server) {
  if (server.command) {
    const args = Array.isArray(server.args) ? server.args.join(' ') : ''
    return `stdio: ${server.command}${args ? ` ${args}` : ''}`
  }
  if (server.url) return `${server.transport || 'http'}: ${server.url}`
  return server.transport || 'stdio'
}

function statusLabel(server) {
  if (server.active === false) return 'inactive'
  return server.status || 'unknown'
}

function statusType(server) {
  const status = statusLabel(server)
  if (status === 'connected') return 'on'
  if (status === 'error') return 'off'
  if (status === 'inactive') return 'default'
  return 'info'
}

function toolAdapters(server) {
  const nativeTools = server.tools?.length || 0
  return nativeTools + (resourceCount(server) > 0 ? 1 : 0) + ((server.prompts?.length || 0) > 0 ? 1 : 0)
}

function resourceCount(server) {
  return (server.resources?.length || 0) + (server.resource_templates?.length || 0)
}

function resourceLabel(resource) {
  return resource.name || resource.uri || resource.uriTemplate || 'resource'
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 20px; }

.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.metric {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--bg1);
}

.metric-value {
  display: block;
  color: var(--t1);
  font-size: 18px;
  font-weight: 700;
  font-family: var(--mono);
  line-height: 1.1;
}

.metric-label {
  color: var(--t3);
  font-size: 11px;
  font-family: var(--mono);
}

.form-section { margin-bottom: 4px; }
.form-section h3 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--t3);
  margin-bottom: 12px;
  font-family: var(--mono);
}

.field { margin-bottom: 12px; min-width: 0; }
.field label {
  display: block;
  font-size: 12px;
  color: var(--t2);
  margin-bottom: 4px;
  font-family: var(--mono);
}
.field input,
.field select {
  width: 100%;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--t1);
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
}
.field input:focus,
.field select:focus { border-color: var(--acc); }

.inline-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
}

.server-card { display: flex; flex-direction: column; gap: 12px; }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.title-stack { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.card-title {
  font-size: 13px;
  font-weight: 700;
  font-family: var(--mono);
  color: var(--t1);
}
.card-meta {
  font-size: 11px;
  color: var(--t3);
  font-family: var(--mono);
  overflow-wrap: anywhere;
}

.inventory {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--t3);
  font-size: 11px;
  font-family: var(--mono);
}
.inventory span {
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 8px;
}

.section { display: flex; flex-direction: column; gap: 6px; }
.section-title {
  color: var(--t3);
  font-size: 11px;
  font-family: var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.rows { display: flex; flex-direction: column; gap: 4px; }
.row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
  gap: 8px;
  align-items: center;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  min-width: 0;
}
.row-name,
.row-meta {
  font-family: var(--mono);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.row-name { color: var(--t1); }
.row-meta { color: var(--t3); }

.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  max-width: 100%;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--t2);
  padding: 3px 8px;
  font-size: 11px;
  font-family: var(--mono);
  overflow-wrap: anywhere;
}

.empty { color: var(--t3); font-size: 13px; }

.notice {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 9px 11px;
  margin-bottom: 12px;
  font-size: 12px;
  font-family: var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.notice.compact { margin-bottom: 0; }
.notice.error {
  color: var(--red);
  background: var(--red-bg);
  border-color: rgba(248,81,73,0.28);
}
.notice.ok {
  color: var(--ok);
  background: var(--ok-bg);
  border-color: rgba(130,184,255,0.28);
}

.form-actions,
.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn-primary { background: var(--acc-bg); color: var(--acc2); border-color: rgba(55,148,255,0.3); }
.btn-primary:hover:not(:disabled) { background: rgba(55,148,255,0.22); }
.btn-ghost { background: none; color: var(--t2); }
.btn-ghost:hover:not(:disabled) { background: var(--bg2); color: var(--t1); }
.btn-danger { background: none; color: var(--red); border-color: rgba(248,81,73,0.25); }
.btn-danger:hover { background: var(--red-bg); }

@media (max-width: 720px) {
  .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .inline-fields,
  .row { grid-template-columns: 1fr; }
  .card-header { flex-direction: column; }
}
</style>
