<template>
  <div class="page">
    <PageHeader title="Adapters" />
    <div class="page-body">
      <section class="form-section">
        <h3>OneBot v11 (Napcat)</h3>
        <p class="desc">
          Reverse WebSocket adapter for Napcat / go-cqhttp compatible clients.
        </p>
        <div class="field">
          <label>Enabled</label>
          <select v-model="form.enabled">
            <option :value="true">
              Yes
            </option>
            <option :value="false">
              No
            </option>
          </select>
        </div>
        <div class="field-row">
          <div class="field">
            <label>WS Reverse Host</label>
            <input
              v-model="form.ws_reverse_host"
              placeholder="0.0.0.0"
            >
          </div>
          <div class="field">
            <label>WS Reverse Port</label>
            <input
              v-model.number="form.ws_reverse_port"
              type="number"
              placeholder="6199"
            >
          </div>
        </div>
        <div class="field">
          <label>Access Token</label>
          <input
            v-model="form.ws_reverse_token"
            type="password"
            placeholder="(optional)"
          >
        </div>
        <StatusBadge :type="obStatus === 'running' ? 'on' : 'off'">
          {{ obStatus || '--' }}
        </StatusBadge>
        <div class="mt-12">
          <button
            class="btn btn-primary"
            @click="save"
          >
            Save (requires restart)
          </button>
        </div>
      </section>

      <section class="form-section">
        <h3>WebChat</h3>
        <p class="desc">
          Built-in adapter for the dashboard chat. Always active when dashboard is enabled.
        </p>
        <StatusBadge type="on">
          running
        </StatusBadge>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import { useApi } from '@/composables/useApi.js'

const api = useApi()

const form = ref({
  enabled: true,
  ws_reverse_host: '0.0.0.0',
  ws_reverse_port: 6199,
  ws_reverse_token: '',
})

const obStatus = ref('')

onMounted(async () => {
  try {
    const d = await api.getAdapter()
    form.value.enabled = d.enabled
    form.value.ws_reverse_host = d.ws_reverse_host || '0.0.0.0'
    form.value.ws_reverse_port = d.ws_reverse_port || 6199
    form.value.ws_reverse_token = ''
    obStatus.value = d.status
  } catch {}
})

async function save() {
  await api.saveAdapter({
    enabled: form.value.enabled,
    ws_reverse_host: form.value.ws_reverse_host,
    ws_reverse_port: form.value.ws_reverse_port,
    ws_reverse_token: form.value.ws_reverse_token,
  })
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

.desc { font-size: 13px; color: var(--t2); margin-bottom: 12px; }

.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--t2); margin-bottom: 4px; font-family: var(--mono); }
.field input, .field select {
  width: 100%; background: var(--bg0); border: 1px solid var(--border);
  border-radius: 6px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field input:focus, .field select:focus { border-color: var(--acc); }

.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }

.mt-12 { margin-top: 12px; }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn-primary { background: var(--acc-bg); color: var(--acc2); border-color: rgba(55,148,255,0.3); }
.btn-primary:hover { background: rgba(55,148,255,0.22); }
</style>
