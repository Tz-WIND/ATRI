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
        <div class="field">
          <label>Admin QQ IDs</label>
          <textarea
            v-model="adminUserIdsText"
            rows="3"
            placeholder="90001&#10;90002"
          />
        </div>
        <div class="field-row">
          <div class="field">
            <label>Private QQ Whitelist</label>
            <textarea
              v-model="privateUserIdsText"
              rows="4"
              placeholder="10001&#10;10002"
            />
          </div>
          <div class="field">
            <label>Group Whitelist</label>
            <textarea
              v-model="groupIdsText"
              rows="4"
              placeholder="123456&#10;654321"
            />
          </div>
        </div>
        <div class="field-row">
          <div class="field checkbox-field">
            <label>Recent Group Context</label>
            <label class="checkbox-control">
              <input
                v-model="form.group_recent_messages.enabled"
                type="checkbox"
              >
              <span>Enabled</span>
            </label>
          </div>
          <div class="field">
            <label>Recent Message Count</label>
            <input
              v-model.number="form.group_recent_messages.max_messages"
              type="number"
              min="0"
              max="50"
              :disabled="!form.group_recent_messages.enabled"
              placeholder="10"
            >
          </div>
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
  admin_user_ids: [],
  group_recent_messages: {
    enabled: true,
    max_messages: 10,
  },
  whitelist: {
    private_user_ids: [],
    group_ids: [],
  },
})

const obStatus = ref('')
const adminUserIdsText = ref('')
const privateUserIdsText = ref('')
const groupIdsText = ref('')

onMounted(async () => {
  try {
    const d = await api.getAdapter()
    form.value.enabled = d.enabled
    form.value.ws_reverse_host = d.ws_reverse_host || '0.0.0.0'
    form.value.ws_reverse_port = d.ws_reverse_port || 6199
    form.value.ws_reverse_token = ''
    form.value.admin_user_ids = normalizeIdList(d.admin_user_ids)
    form.value.group_recent_messages = normalizeRecentGroupMessages(d.group_recent_messages)
    form.value.whitelist = normalizeWhitelist(d.whitelist)
    adminUserIdsText.value = form.value.admin_user_ids.join('\n')
    privateUserIdsText.value = form.value.whitelist.private_user_ids.join('\n')
    groupIdsText.value = form.value.whitelist.group_ids.join('\n')
    obStatus.value = d.status
  } catch {}
})

function normalizeRecentGroupMessages(value) {
  const config = value && typeof value === 'object' ? value : {}
  return {
    enabled: config.enabled ?? true,
    max_messages: Number.isFinite(Number(config.max_messages))
      ? Math.max(0, Number(config.max_messages))
      : 10,
  }
}

function normalizeIdList(value) {
  if (Array.isArray(value)) {
    return value.map(item => String(item).trim()).filter(Boolean)
  }
  if (typeof value === 'string') {
    return value.split(/[\s,，]+/).map(item => item.trim()).filter(Boolean)
  }
  return []
}

function normalizeWhitelist(value) {
  const config = value && typeof value === 'object' ? value : {}
  return {
    private_user_ids: normalizeIdList(config.private_user_ids),
    group_ids: normalizeIdList(config.group_ids),
  }
}

async function save() {
  await api.saveAdapter({
    enabled: form.value.enabled,
    ws_reverse_host: form.value.ws_reverse_host,
    ws_reverse_port: form.value.ws_reverse_port,
    ws_reverse_token: form.value.ws_reverse_token,
    admin_user_ids: normalizeIdList(adminUserIdsText.value),
    group_recent_messages: normalizeRecentGroupMessages(form.value.group_recent_messages),
    whitelist: {
      private_user_ids: normalizeIdList(privateUserIdsText.value),
      group_ids: normalizeIdList(groupIdsText.value),
    },
  })
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.page-body { flex: 1; overflow-y: auto; padding: 22px 24px; }

.form-section {
  max-width: 820px;
  margin-bottom: 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.032);
  padding: 16px;
}
.form-section h3 {
  font-size: 14px; letter-spacing: 0;
  color: var(--t1); margin-bottom: 6px; font-weight: 650;
}

.desc { font-size: 13px; color: var(--t2); margin-bottom: 12px; }

.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--t2); margin-bottom: 4px; font-family: var(--mono); }
.field input:not([type="checkbox"]), .field select {
  width: 100%; background: rgba(24, 24, 24, 0.66); border: 1px solid var(--border-input);
  border-radius: 7px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field input:not([type="checkbox"]):focus, .field select:focus { border-color: rgba(158, 191, 255, 0.5); box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12); }
.field textarea {
  width: 100%; min-height: 92px; resize: vertical;
  background: rgba(24, 24, 24, 0.66); border: 1px solid var(--border-input);
  border-radius: 7px; color: var(--t1); padding: 8px 12px; font-size: 13px;
  font-family: var(--mono); outline: none;
}
.field textarea:focus { border-color: rgba(158, 191, 255, 0.5); box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12); }

.checkbox-field { display: flex; flex-direction: column; }
.checkbox-control {
  min-height: 35px; display: flex; align-items: center; gap: 8px;
  color: var(--t1); font-size: 13px; font-family: var(--mono);
}
.checkbox-control input {
  width: 16px; height: 16px; margin: 0; accent-color: var(--acc2);
}

.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }

.mt-12 { margin-top: 12px; }

.btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; font-size: 12px; font-weight: 600; font-family: var(--mono);
  transition: all 0.12s;
}
.btn-primary { background: var(--acc-bg); color: var(--acc2); border-color: rgba(125,168,232,0.3); }
.btn-primary:hover { background: var(--acc-bg-strong); }
</style>
