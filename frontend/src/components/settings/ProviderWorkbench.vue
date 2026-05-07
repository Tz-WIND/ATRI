<template>
  <div class="prov-workbench">
    <ProviderSidebar
      :providers="providerList"
      :selected-name="selectedName"
      @select="selectProvider"
      @add="showForm = true"
      @delete="handleDeleteProvider"
    />
    <div class="prov-divider" />
    <ProviderDetail
      v-if="selectedProvider"
      :provider="selectedProvider"
      :name="selectedName"
      :active-models="activeModels"
      :active-model="activeModel"
      :fetching-models="fetchingModels"
      :fetch-error="modelFetchError"
      @save="handleSaveProvider"
      @delete="handleDeleteCurrent"
      @fetch-models="handleFetchModels"
      @activate-model="handleActivateModel"
      @deactivate-model="handleDeactivateModel"
    />
    <div
      v-else
      class="prov-empty"
    >
      <svg
        viewBox="0 0 24 24"
        width="40"
        height="40"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        style="color:var(--t3)"
      >
        <circle
          cx="12"
          cy="12"
          r="3"
        /><path d="M12 1v4m0 14v4M4.22 4.22l2.83 2.83m9.9 9.9l2.83 2.83M1 12h4m14 0h4M4.22 19.78l2.83-2.83m9.9-9.9l2.83-2.83" />
      </svg>
      <span>Select a provider or add a new one</span>
    </div>

    <!-- Add Provider Form -->
    <div
      v-if="showForm"
      class="modal-overlay"
      @click.self="showForm = false"
    >
      <div class="modal-card">
        <div class="card-header">
          <span class="card-title">Add Provider</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Provider Name</label>
            <input
              v-model="addForm.name"
              placeholder="e.g. DeepSeek, OpenAI, Ollama..."
            >
          </div>
          <div class="field">
            <label>API Format</label>
            <select v-model="addForm.api_format">
              <option value="openai">
                OpenAI Compatible
              </option>
              <option value="anthropic">
                Anthropic
              </option>
            </select>
          </div>
          <div class="field">
            <label>Base URL</label>
            <input
              v-model="addForm.base_url"
              placeholder="https://api.deepseek.com/v1"
            >
          </div>
          <div class="field">
            <label>API Key</label>
            <input
              v-model="addForm.api_key"
              type="password"
              placeholder="sk-..."
            >
          </div>
          <div class="form-actions">
            <button
              class="btn btn-primary"
              @click="handleAddProvider"
            >
              Save
            </button>
            <button
              class="btn btn-ghost"
              @click="showForm = false"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ProviderSidebar from './ProviderSidebar.vue'
import ProviderDetail from './ProviderDetail.vue'
import { useProviders } from '@/composables/useProviders.js'

const {
  selectedName,
  selectedProvider,
  providerList,
  activeModels,
  activeModel,
  selectProvider,
  saveProvider,
  removeProvider,
  fetchModels,
  activateModel,
  deactivateModel,
} = useProviders()

const showForm = ref(false)
const fetchingModels = ref(false)
const modelFetchError = ref('')
const addForm = ref({
  name: '',
  api_format: 'openai',
  base_url: '',
  api_key: '',
})

async function handleAddProvider() {
  if (!addForm.value.name.trim()) return
  await saveProvider({ ...addForm.value })
  showForm.value = false
  addForm.value = { name: '', api_format: 'openai', base_url: '', api_key: '' }
}

async function handleSaveProvider(data) {
  modelFetchError.value = ''
  await saveProvider(data)
}

async function handleDeleteProvider(name) {
  if (!confirm(`Delete provider "${name}"?`)) return
  await removeProvider(name)
}

async function handleDeleteCurrent() {
  if (!selectedName.value || !confirm(`Delete provider "${selectedName.value}"?`)) return
  await removeProvider(selectedName.value)
}

async function handleFetchModels(data) {
  if (!selectedName.value) return
  fetchingModels.value = true
  modelFetchError.value = ''
  try {
    await fetchModels(data || { name: selectedName.value })
  } catch (e) {
    modelFetchError.value = e.message || String(e)
  } finally {
    fetchingModels.value = false
  }
}

async function handleActivateModel(provider, model) {
  await activateModel(provider, model)
}

async function handleDeactivateModel(provider, model) {
  await deactivateModel(provider, model)
}
</script>

<style scoped>
.prov-workbench {
  display: grid;
  grid-template-columns: 260px 1px 1fr;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg1);
  min-height: 520px;
  overflow: hidden;
}

.prov-divider {
  background: var(--border);
}

.prov-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--t3);
  font-size: 13px;
  gap: 8px;
  padding: 20px;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
}

.modal-card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 12px;
  width: 420px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5);
}

.card-header {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}

.card-title {
  font-size: 13px;
  font-weight: 600;
  font-family: var(--mono);
  color: var(--t1);
}

.card-body {
  padding: 14px 18px;
}

.field {
  margin-bottom: 12px;
}

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
.field select:focus {
  border-color: var(--acc);
}

.form-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.btn {
  padding: 6px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}

.btn-primary {
  background: rgba(63, 185, 80, 0.15);
  color: var(--green);
  border-color: rgba(63, 185, 80, 0.3);
}

.btn-primary:hover { background: rgba(63, 185, 80, 0.25); }

.btn-ghost {
  background: none;
  color: var(--t2);
}

.btn-ghost:hover {
  background: var(--bg2);
  color: var(--t1);
}

@media (max-width: 768px) {
  .prov-workbench {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1px auto;
  }
}
</style>
