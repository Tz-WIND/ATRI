<template>
  <div class="model-pool-section">
    <div class="pool-head">
      <div>
        <div class="pool-title">
          {{ title }}
        </div>
        <p class="pool-desc">
          {{ description }}
        </p>
      </div>
      <span class="pool-count">{{ poolModels.length }} enabled</span>
    </div>

    <div class="pool-add-row">
      <label class="pool-field">
        <span>Provider</span>
        <select v-model="selectedProvider">
          <option
            v-for="provider in providerList"
            :key="provider.name"
            :value="provider.name"
          >
            {{ provider.name }}
          </option>
        </select>
      </label>
      <label class="pool-field model-picker">
        <span>Model</span>
        <select
          v-model="selectedModel"
          :disabled="candidateModels.length === 0"
        >
          <option
            v-if="candidateModels.length === 0"
            value=""
          >
            No models fetched
          </option>
          <option
            v-for="model in candidateModels"
            :key="model"
            :value="model"
          >
            {{ model }}
          </option>
        </select>
      </label>
      <button
        class="btn btn-primary pool-add-button"
        :disabled="!canAdd"
        @click="handleAdd"
      >
        Add
      </button>
    </div>

    <div
      v-if="providerList.length === 0"
      class="empty"
    >
      Add a provider first.
    </div>
    <div
      v-else-if="poolModels.length === 0"
      class="empty"
    >
      {{ emptyText }}
    </div>

    <div
      v-for="entry in poolModels"
      :key="`${entry.provider || ''}:${entry.model}`"
      class="pool-row"
      role="button"
      tabindex="0"
      @click="openConfig(entry)"
      @keydown.enter.prevent="openConfig(entry)"
      @keydown.space.prevent="openConfig(entry)"
    >
      <div class="pool-model-info">
        <div :class="['pool-model-name', { active: isCurrent(entry) }]">
          {{ entry.model }}
        </div>
        <div class="pool-model-meta">
          <span v-if="entry.provider">{{ entry.provider }}</span>
          <span
            v-if="isCurrent(entry)"
            class="badge-current"
          >current</span>
        </div>
      </div>
      <div class="pool-actions">
        <button
          v-if="!isCurrent(entry)"
          class="btn btn-primary"
          @click.stop="selectPoolModel(pool, entry.provider || '', entry.model)"
        >
          Activate
        </button>
        <button
          class="btn btn-ghost"
          @click.stop="openConfig(entry)"
        >
          Configure
        </button>
        <button
          class="btn btn-remove"
          @click.stop="deactivatePoolModel(pool, entry.provider || '', entry.model)"
        >
          Remove
        </button>
      </div>
    </div>

    <div
      v-if="configEntry"
      class="config-modal"
      @click.self="closeConfig"
    >
      <div class="config-card">
        <div class="config-head">
          <div>
            <div class="config-title">
              {{ configEntry.model }}
            </div>
            <div class="config-subtitle">
              {{ configEntry.provider || 'Default provider' }} / {{ title }}
            </div>
          </div>
          <button
            class="btn-icon"
            title="Close"
            @click="closeConfig"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </svg>
          </button>
        </div>

        <div class="config-body">
          <label
            v-for="field in fieldSpecs"
            :key="field.key"
            class="pool-field"
          >
            <span>{{ field.label }}</span>
            <select
              v-if="field.type === 'select'"
              v-model="configDraft[field.key]"
            >
              <option
                v-for="option in field.options"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
            <input
              v-else
              v-model.number="configDraft[field.key]"
              :type="field.type"
              :min="field.min"
              :step="field.step"
            >
          </label>
        </div>

        <div class="config-actions">
          <button
            class="btn btn-ghost"
            @click="closeConfig"
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            @click="saveConfig"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useProviders } from '@/composables/useProviders.js'

const props = defineProps({
  pool: { type: String, required: true },
  title: { type: String, required: true },
  description: { type: String, required: true },
  models: { type: Array, default: () => [] },
  activeModel: { type: String, default: '' },
  activeProvider: { type: String, default: '' },
  emptyText: { type: String, default: 'No models enabled in this pool.' },
})

const CHAT_DEFAULTS = {
  max_tokens: 4096,
  temperature: 0,
  max_context_tokens: 128000,
  max_rounds: 50,
}

const EMBEDDING_DEFAULTS = {
  dimensions: 1536,
  batch_size: 64,
  encoding_format: 'float',
}

const RERANK_DEFAULTS = {
  top_n: 5,
  score_threshold: 0,
  max_input_tokens: 8192,
}

const {
  providerList,
  activatePoolModel,
  deactivatePoolModel,
  selectPoolModel,
  savePoolModelConfig,
  isPoolModelActive,
} = useProviders()

const selectedProvider = ref('')
const selectedModel = ref('')
const configEntry = ref(null)
const configDraft = ref({})

const poolModels = computed(() => (
  props.models.filter(entry => entry && entry.model)
))

const selectedProviderConfig = computed(() => (
  providerList.value.find(provider => provider.name === selectedProvider.value) || null
))

const candidateModels = computed(() => (
  (selectedProviderConfig.value?.models || []).filter(model => typeof model === 'string' && model)
))

const defaults = computed(() => {
  if (props.pool === 'chat') return CHAT_DEFAULTS
  if (props.pool === 'embedding') return EMBEDDING_DEFAULTS
  return RERANK_DEFAULTS
})

const fieldSpecs = computed(() => {
  if (props.pool === 'chat') {
    return [
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', min: 1, step: 1 },
      { key: 'temperature', label: 'Temperature', type: 'number', min: 0, step: 0.1 },
      { key: 'max_context_tokens', label: 'Max Context Tokens', type: 'number', min: 1, step: 1 },
      { key: 'max_rounds', label: 'Max Rounds', type: 'number', min: 1, step: 1 },
    ]
  }
  if (props.pool === 'embedding') {
    return [
      { key: 'dimensions', label: 'Dimensions', type: 'number', min: 1, step: 1 },
      { key: 'batch_size', label: 'Batch Size', type: 'number', min: 1, step: 1 },
      {
        key: 'encoding_format',
        label: 'Encoding Format',
        type: 'select',
        options: [
          { value: 'float', label: 'Float' },
          { value: 'base64', label: 'Base64' },
        ],
      },
    ]
  }
  return [
    { key: 'top_n', label: 'Top N', type: 'number', min: 1, step: 1 },
    { key: 'score_threshold', label: 'Score Threshold', type: 'number', min: 0, step: 0.01 },
    { key: 'max_input_tokens', label: 'Max Input Tokens', type: 'number', min: 1, step: 1 },
  ]
})

const canAdd = computed(() => (
  Boolean(selectedProvider.value && selectedModel.value) &&
  !isPoolModelActive(props.pool, selectedProvider.value, selectedModel.value)
))

watch(providerList, (providers) => {
  if (providers.length === 0) {
    selectedProvider.value = ''
    selectedModel.value = ''
    return
  }
  if (!providers.some(provider => provider.name === selectedProvider.value)) {
    selectedProvider.value = providers[0].name
  }
}, { immediate: true })

watch([selectedProvider, candidateModels], () => {
  if (candidateModels.value.length === 0) {
    selectedModel.value = ''
    return
  }
  if (!candidateModels.value.includes(selectedModel.value)) {
    selectedModel.value = candidateModels.value[0]
  }
}, { immediate: true })

function isCurrent(entry) {
  return entry.model === props.activeModel && (entry.provider || '') === props.activeProvider
}

async function handleAdd() {
  if (!canAdd.value) return
  await activatePoolModel(props.pool, selectedProvider.value, selectedModel.value)
}

function openConfig(entry) {
  configEntry.value = entry
  configDraft.value = { ...defaults.value, ...(entry.config || {}) }
}

function closeConfig() {
  configEntry.value = null
  configDraft.value = {}
}

async function saveConfig() {
  if (!configEntry.value) return
  await savePoolModelConfig(
    props.pool,
    configEntry.value.provider || '',
    configEntry.value.model,
    configDraft.value,
  )
  closeConfig()
}
</script>

<style scoped>
.model-pool-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pool-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.pool-title {
  color: var(--t1);
  font-size: 13px;
  font-weight: 650;
}

.pool-desc {
  color: var(--t3);
  font-size: 12px;
  line-height: 1.5;
  margin-top: 3px;
}

.pool-count {
  flex-shrink: 0;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
}

.pool-add-row {
  display: grid;
  grid-template-columns: minmax(150px, 0.45fr) minmax(220px, 1fr) auto;
  gap: 10px;
  align-items: end;
}

.pool-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--t2);
  font-size: 12px;
  font-weight: 600;
}

.pool-field span {
  color: var(--t2);
}

.pool-field input,
.pool-field select {
  width: 100%;
  height: 36px;
  border: 1px solid var(--border-input);
  border-radius: 7px;
  background: rgba(24, 24, 24, 0.62);
  color: var(--t1);
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
}

.pool-field input:focus,
.pool-field select:focus {
  border-color: rgba(158, 191, 255, 0.5);
  background: rgba(24, 24, 24, 0.82);
  box-shadow: 0 0 0 1px rgba(158, 191, 255, 0.12);
}

.pool-add-button {
  height: 36px;
}

.empty {
  color: var(--t3);
  font-size: 12px;
  padding: 4px 0;
}

.pool-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 0;
  border-top: 1px solid var(--border-light);
  cursor: pointer;
}

.pool-row:hover .pool-model-name {
  color: var(--acc2);
}

.pool-model-info {
  min-width: 0;
  flex: 1;
}

.pool-model-name {
  color: var(--t1);
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pool-model-name.active {
  color: var(--acc2);
}

.pool-model-meta {
  display: flex;
  gap: 8px;
  color: var(--t3);
  font-size: 11px;
}

.badge-current {
  color: var(--ok);
  font-family: var(--mono);
}

.pool-actions {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.btn {
  min-height: 30px;
  padding: 0 11px;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  transition: background 0.15s, border-color 0.15s, color 0.15s, opacity 0.15s;
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.btn-primary {
  background: var(--acc-bg);
  color: var(--acc2);
  border-color: rgba(125, 168, 232, 0.3);
}

.btn-primary:hover:not(:disabled) {
  background: var(--acc-bg-strong);
}

.btn-ghost {
  background: transparent;
  color: var(--t2);
}

.btn-ghost:hover {
  background: var(--bg-100);
  color: var(--t1);
}

.btn-remove {
  flex-shrink: 0;
  background: var(--red-bg);
  color: var(--red);
  border-color: rgba(255, 141, 127, 0.25);
}

.btn-remove:hover {
  background: rgba(255, 141, 127, 0.18);
}

.config-modal {
  position: fixed;
  inset: 0;
  z-index: 520;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px;
  background: rgba(24, 24, 24, 0.45);
  backdrop-filter: blur(4px);
}

.config-card {
  width: min(440px, 100%);
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--glass-strong);
  box-shadow: var(--shadow-panel);
  overflow: hidden;
}

.config-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 15px 16px;
  border-bottom: 1px solid var(--border);
}

.config-title {
  color: var(--t1);
  font-family: var(--mono);
  font-size: 14px;
  font-weight: 650;
}

.config-subtitle {
  color: var(--t3);
  font-size: 11px;
  margin-top: 3px;
}

.config-body {
  display: grid;
  gap: 12px;
  padding: 15px 16px;
}

.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 13px 16px;
  border-top: 1px solid var(--border);
}

.btn-icon {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
}

.btn-icon:hover {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-light);
}

.btn-icon svg {
  width: 15px;
  height: 15px;
}

@media (max-width: 760px) {
  .pool-add-row {
    grid-template-columns: 1fr;
  }

  .pool-add-button {
    width: 100%;
  }

  .pool-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .pool-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
