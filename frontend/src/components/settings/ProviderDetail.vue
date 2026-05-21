<template>
  <div class="prov-detail">
    <div class="detail-head">
      <div>
        <div class="detail-title">
          {{ name }}
        </div>
        <div class="detail-subtitle">
          {{ provider.base_url || 'No base URL set' }}
        </div>
      </div>
      <div class="detail-actions">
        <button
          class="btn btn-primary"
          @click="handleSave"
        >
          Save
        </button>
        <button
          class="btn btn-danger"
          @click="$emit('delete')"
        >
          Delete
        </button>
      </div>
    </div>
    <div class="detail-body">
      <!-- Connection -->
      <div>
        <div class="section-title">
          Connection
        </div>
        <div class="field">
          <label>Base URL</label>
          <input
            v-model="form.base_url"
            placeholder="https://api.openai.com/v1"
          >
        </div>
        <div class="field">
          <label>API Key</label>
          <input
            v-model="form.api_key"
            type="password"
            :placeholder="provider.api_key ? '••••••••(unchanged)' : 'sk-...'"
          >
        </div>
        <div class="field">
          <label>API Format</label>
          <select v-model="form.api_format">
            <option value="openai">
              OpenAI Compatible
            </option>
            <option value="anthropic">
              Anthropic
            </option>
          </select>
        </div>
      </div>

      <!-- Models -->
      <div class="models-section">
        <div class="models-toolbar">
          <div>
            <div
              class="section-title"
              style="margin-bottom:2px"
            >
              Models
            </div>
            <span class="model-count">{{ models.length }} available</span>
          </div>
          <div class="models-actions">
            <input
              v-model="modelSearch"
              type="text"
              placeholder="Search..."
              class="search-input"
            >
            <button
              class="btn btn-primary"
              :disabled="fetchingModels"
              @click="handleFetchModels"
            >
              {{ fetchingModels ? 'Fetching...' : 'Get Models' }}
            </button>
          </div>
        </div>
        <div
          v-if="fetchError"
          class="models-error"
        >
          {{ fetchError }}
        </div>
        <div class="models-list">
          <div
            v-if="filteredModels.length === 0"
            class="models-empty"
          >
            {{ models.length ? 'No matches' : 'No models fetched yet. Click "Get Models" to fetch.' }}
          </div>
          <div
            v-for="m in filteredModels"
            :key="m"
            class="model-row"
          >
            <div class="model-info">
              <div :class="['model-name', { active: m === activeModel && name === activeProvider }]">
                {{ m }}
              </div>
            </div>
            <div class="model-actions">
              <template v-if="isModelActive(name, m)">
                <span class="model-state">
                  {{ m === activeModel && name === activeProvider ? '✓ current' : 'enabled' }}
                </span>
                <button
                  class="btn btn-danger"
                  @click="$emit('deactivate-model', name, m)"
                >
                  Remove
                </button>
              </template>
              <button
                v-else
                class="btn btn-primary"
                @click="$emit('activate-model', name, m)"
              >
                Enable
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  provider: { type: Object, default: null },
  name: { type: String, default: '' },
  activeModels: { type: Array, default: () => [] },
  activeModel: { type: String, default: '' },
  activeProvider: { type: String, default: '' },
  fetchingModels: { type: Boolean, default: false },
  fetchError: { type: String, default: '' },
})

const emit = defineEmits(['save', 'delete', 'fetch-models', 'activate-model', 'deactivate-model'])

const modelSearch = ref('')

const form = ref({
  base_url: '',
  api_key: '',
  api_format: 'openai',
})

// Sync form when provider changes
watch(() => props.provider, (p) => {
  if (p) {
    form.value.base_url = p.base_url || ''
    form.value.api_key = ''
    form.value.api_format = p.api_format || 'openai'
  }
}, { immediate: true })

const models = computed(() => props.provider?.models || [])

const filteredModels = computed(() => {
  const q = modelSearch.value.toLowerCase()
  if (!q) return models.value
  return models.value.filter(m => m.toLowerCase().includes(q))
})

function isModelActive(provider, model) {
  return props.activeModels.some(m => m.model === model && m.provider === provider)
}

function handleSave() {
  emit('save', {
    name: props.name,
    base_url: form.value.base_url,
    api_key: form.value.api_key,
    api_format: form.value.api_format,
  })
}

function handleFetchModels() {
  emit('fetch-models', {
    name: props.name,
    base_url: form.value.base_url,
    api_key: form.value.api_key,
    api_format: form.value.api_format,
  })
}
</script>

<style scoped>
.prov-detail {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow-y: auto;
  background: rgba(24, 24, 24, 0.12);
}

.detail-head {
  padding: 16px 18px 13px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--border);
}

.detail-title {
  font-size: 18px;
  font-weight: 650;
  color: var(--t1);
}

.detail-subtitle {
  font-size: 12px;
  color: var(--t3);
  margin-top: 4px;
}

.detail-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.detail-body {
  padding: 17px 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.section-title {
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0;
  color: var(--t3);
  margin-bottom: 10px;
}

.field {
  margin-bottom: 12px;
}

.field label {
  display: block;
  font-size: 12px;
  color: var(--t2);
  margin-bottom: 4px;
  font-weight: 600;
}

.field input,
.field select {
  width: 100%;
  background: rgba(24, 24, 24, 0.66);
  border: 1px solid var(--border-input);
  border-radius: 7px;
  color: var(--t1);
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
}

.field input:focus,
.field select:focus {
  border-color: rgba(158, 191, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.models-section {
  border-top: 1px solid var(--border);
  padding-top: 16px;
}

.models-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.models-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.model-count {
  font-size: 11px;
  color: var(--t3);
}

.search-input {
  width: 140px;
  background: rgba(24, 24, 24, 0.66);
  border: 1px solid var(--border-input);
  border-radius: 7px;
  padding: 5px 10px;
  color: var(--t1);
  font-size: 12px;
  font-family: var(--mono);
  outline: none;
}

.search-input:focus {
  border-color: rgba(158, 191, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.models-list {
  margin-top: 10px;
}

.models-empty {
  padding: 16px;
  text-align: center;
  color: var(--t3);
  font-size: 12px;
}

.models-error {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(255, 141, 127, 0.25);
  border-radius: 7px;
  background: var(--red-bg);
  color: var(--red);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 0;
  border-bottom: 1px solid var(--border-light);
}

.model-row:last-child { border-bottom: none; }

.model-info { flex: 1; min-width: 0; }

.model-name {
  font-size: 13px;
  font-weight: 650;
  font-family: var(--mono);
  color: var(--t1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-name.active { color: var(--acc2); }

.model-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.model-state {
  font-size: 11px;
  color: var(--ok);
  font-family: var(--mono);
  margin-right: 6px;
}

.btn {
  padding: 4px 12px;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.btn-primary {
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(125, 168, 232, 0.3);
}

.btn-primary:hover { background: var(--acc-bg-strong); }

.btn-danger {
  background: var(--red-bg);
  color: var(--red);
  border-color: rgba(255, 141, 127, 0.25);
  font-size: 10px;
  padding: 2px 6px;
}

.btn-danger:hover { background: rgba(255, 141, 127, 0.18); }
</style>
